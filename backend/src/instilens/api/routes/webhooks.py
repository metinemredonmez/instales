"""JWT-protected integration management plus a raw-body, HMAC-authenticated receiver."""

import json
import secrets
from uuid import uuid4

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.security import APIKeyHeader
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select, update
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from instilens.api.deps import current_user, get_session
from instilens.api.hardening import client_ip, hit
from instilens.domain.enums import WebhookDirection as Direction
from instilens.domain.enums import WebhookStatus as Status
from instilens.domain.models import User, WebhookAttempt, WebhookEndpoint, WebhookMessage
from instilens.services import webhooks

router = APIRouter(prefix="/api/v1/webhooks", tags=["webhooks"], dependencies=[Depends(current_user)])
signed_header = APIKeyHeader(name="X-Instilens-Signature", scheme_name="WebhookSignature", auto_error=False,
                            description="sha256=HMAC-SHA256(secret, timestamp + '.' + exact raw body). A session JWT is not used on the receiver.")
EVENT_BODY = {"requestBody": {"required": True, "content": {"application/json": {"schema": webhooks.EventEnvelope.model_json_schema()}}}}


class EndpointBody(BaseModel):
    name: str = Field(min_length=1, max_length=80, examples=["My integration"])
    target_url: str | None = Field(None, max_length=2048, examples=["https://example.com/hooks/instilens"])


class EndpointPatch(BaseModel):
    enabled: bool


class EndpointResponse(BaseModel):
    id: str
    name: str
    target_url: str | None
    incoming_path: str
    enabled: bool
    created_at: str


class EndpointCreated(EndpointResponse):
    signing_secret: str


class MessageResponse(BaseModel):
    id: int
    endpoint_id: str
    event_id: str
    event_type: str
    direction: Direction
    status: Status
    attempts: int
    created_at: str
    next_attempt_at: str | None


class QueuedResponse(MessageResponse):
    duplicate: bool


class AttemptResponse(BaseModel):
    id: int
    status_code: int | None
    error: str | None
    created_at: str


class MessageDetail(MessageResponse):
    event: webhooks.EventEnvelope
    deliveries: list[AttemptResponse]


class ReceivedResponse(BaseModel):
    received: bool
    id: int
    duplicate: bool


def _endpoint(session: Session, user: User, endpoint_id: str) -> WebhookEndpoint:
    endpoint = session.scalar(select(WebhookEndpoint).where(WebhookEndpoint.id == endpoint_id, WebhookEndpoint.owner_id == user.id))
    if endpoint is None:
        raise HTTPException(404, "webhook endpoint not found")
    return endpoint


def _rate(key: str, limit: int, seconds: int = 60):
    if not hit("webhook:" + key, limit, seconds):
        raise HTTPException(429, "webhook rate limit exceeded", headers={"Retry-After": str(seconds)})


def _write_user(user: User = Depends(current_user)) -> User:
    _rate(f"write:{user.id}", 30)
    return user


async def _body(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(415, "application/json is required")
    if request.headers.get("content-encoding", "identity") != "identity":
        raise HTTPException(415, "compressed webhook bodies are not supported")
    data = bytearray()
    async for chunk in request.stream():
        data.extend(chunk)
        if len(data) > webhooks.MAX_BODY:
            raise HTTPException(413, "event exceeds 64 KiB")
    return bytes(data)


def _event(body: bytes) -> webhooks.EventEnvelope:
    def invalid_constant(_value):
        raise ValueError("non-finite JSON number")
    try:
        return webhooks.EventEnvelope.model_validate(json.loads(body, parse_constant=invalid_constant))
    except (ValidationError, ValueError, UnicodeError, RecursionError) as exc:
        raise HTTPException(422, "expected a JSON event with id, type and object data") from exc


@router.get("/endpoints", response_model=list[EndpointResponse], response_model_exclude_none=True)
def list_endpoints(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """List your integrations. Signing secrets are never returned here."""
    return [webhooks.endpoint_json(row) for row in session.scalars(select(WebhookEndpoint).where(
        WebhookEndpoint.owner_id == user.id,
    ).order_by(WebhookEndpoint.created_at.desc()))]


@router.post("/endpoints", status_code=201, response_model=EndpointCreated, response_model_exclude_none=True)
def create_endpoint(body: EndpointBody, user: User = Depends(_write_user), session: Session = Depends(get_session)):
    """Create an inbox/outbox. Save signing_secret now: only creation and rotation disclose it.

    target_url may be null for an incoming-only integration. Outgoing targets must be public HTTPS on port 443.
    """
    try:
        return webhooks.endpoint_json(webhooks.create_endpoint(session, user, body.name, body.target_url), reveal=True)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/endpoints/{endpoint_id}", response_model=EndpointResponse, response_model_exclude_none=True)
def set_enabled(endpoint_id: str, body: EndpointPatch, user: User = Depends(_write_user), session: Session = Depends(get_session)):
    """Pause/resume incoming events and pending deliveries. An already in-flight request may finish."""
    endpoint = _endpoint(session, user, endpoint_id)
    endpoint.enabled = body.enabled
    session.flush()
    return webhooks.endpoint_json(endpoint)


@router.post("/endpoints/{endpoint_id}/rotate", response_model=EndpointCreated, response_model_exclude_none=True)
def rotate(endpoint_id: str, user: User = Depends(_write_user), session: Session = Depends(get_session)):
    """Replace the shared signing secret immediately; update the external sender/receiver too."""
    endpoint = _endpoint(session, user, endpoint_id)
    endpoint.secret_seed = secrets.token_hex(32)
    session.flush()
    return webhooks.endpoint_json(endpoint, reveal=True)


def _queue(session: Session, endpoint: WebhookEndpoint, event: webhooks.EventEnvelope) -> dict:
    if not endpoint.enabled:
        raise HTTPException(409, "endpoint is paused")
    try:
        message, duplicate = webhooks.store_message(session, endpoint, event, Direction.OUTGOING)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {**webhooks.message_json(message), "duplicate": duplicate}


@router.post("/endpoints/{endpoint_id}/send", status_code=202, response_model=QueuedResponse, openapi_extra=EVENT_BODY)
async def send_event(endpoint_id: str, request: Request, user: User = Depends(_write_user), session: Session = Depends(get_session)):
    """Queue a signed event. HTTP 202 means queued, NOT delivered. Reuse id for safe client retries.

    The running `instilens scheduler` sends pending events every 30 seconds, with up to 5 attempts.
    """
    event = _event(await _body(request))
    def queue():
        return _queue(session, _endpoint(session, user, endpoint_id), event)
    return await run_in_threadpool(queue)


@router.post("/endpoints/{endpoint_id}/test", status_code=202, response_model=QueuedResponse)
def test_event(endpoint_id: str, user: User = Depends(_write_user), session: Session = Depends(get_session)):
    """Queue an explicit webhook.test event to the configured external receiver."""
    return _queue(session, _endpoint(session, user, endpoint_id), webhooks.EventEnvelope(
        id=str(uuid4()), type="webhook.test", data={"message": "InstiLens connection test"},
    ))


@router.get("/endpoints/{endpoint_id}/messages", response_model=list[MessageResponse])
def messages(endpoint_id: str, before: int | None = Query(None, ge=1), limit: int = Query(25, ge=1, le=100),
             user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Inbox and outbox history, newest first. Pass the last id as `before` for the next page."""
    _endpoint(session, user, endpoint_id)
    query = select(WebhookMessage).where(WebhookMessage.endpoint_id == endpoint_id)
    if before:
        query = query.where(WebhookMessage.id < before)
    return [webhooks.message_json(row) for row in session.scalars(query.order_by(WebhookMessage.id.desc()).limit(limit))]


def _message(session: Session, user: User, message_id: int) -> WebhookMessage:
    row = session.scalar(select(WebhookMessage).join(WebhookEndpoint).where(
        WebhookMessage.id == message_id, WebhookEndpoint.owner_id == user.id,
    ))
    if row is None:
        raise HTTPException(404, "webhook message not found")
    return row


@router.get("/messages/{message_id}", response_model=MessageDetail)
def message_detail(message_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Original transport event and delivery attempts. Incoming payloads remain untrusted data."""
    row = _message(session, user, message_id)
    attempts = session.scalars(select(WebhookAttempt).where(WebhookAttempt.message_id == row.id).order_by(WebhookAttempt.id))
    return {**webhooks.message_json(row, body=True), "deliveries": [
        {"id": a.id, "status_code": a.status_code, "error": a.error, "created_at": a.created_at.isoformat()} for a in attempts
    ]}


@router.post("/messages/{message_id}/retry", status_code=202, response_model=MessageResponse)
def retry(message_id: int, user: User = Depends(_write_user), session: Session = Depends(get_session)):
    """Requeue a failed event; retain its id, attempts and remaining automatic retry budget."""
    row = _message(session, user, message_id)
    endpoint = _endpoint(session, user, row.endpoint_id)
    if not endpoint.enabled:
        raise HTTPException(409, "endpoint is paused")
    changed = session.execute(update(WebhookMessage).where(
        WebhookMessage.id == row.id, WebhookMessage.status == Status.FAILED,
        WebhookMessage.direction == Direction.OUTGOING,
    ).values(status=Status.PENDING, next_attempt_at=webhooks.now()))
    if changed.rowcount != 1:
        raise HTTPException(409, "only failed outgoing events can be retried")
    session.flush()
    session.refresh(row)
    return webhooks.message_json(row)


async def receive(endpoint_id: str, request: Request,
                  timestamp: str = Header("", alias="X-Instilens-Timestamp"),
                  supplied: str | None = Depends(signed_header), session: Session = Depends(get_session)):
    """Accept a signed external event (64 KiB max, timestamp within 5 minutes, unique id).

    Signature: `sha256=` + hex HMAC-SHA256(signing_secret, timestamp + '.' + EXACT raw body).
    No Bearer token is needed here: the endpoint's HMAC secret authenticates the sender.
    Accepted events are stored in your inbox only; they do not update financial tables.
    """
    await run_in_threadpool(_rate, f"incoming-ip:{client_ip(request)}", 240)
    body = await _body(request)
    def accept():
        _rate(f"incoming:{endpoint_id}", 120)
        endpoint = session.scalar(select(WebhookEndpoint).join(User).where(
            WebhookEndpoint.id == endpoint_id, WebhookEndpoint.enabled.is_(True), User.is_active.is_(True),
        ))
        if endpoint is None:
            raise HTTPException(404, "webhook endpoint not found")
        try:
            webhooks.verify(endpoint, body, timestamp, supplied or "")
        except webhooks.WebhookError as exc:
            raise HTTPException(401, str(exc)) from exc
        _rate(f"daily:{endpoint.owner_id}", 10000, 86400)
        try:
            message, duplicate = webhooks.store_message(session, endpoint, _event(body), Direction.INCOMING)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {"received": True, "id": message.id, "duplicate": duplicate}
    return await run_in_threadpool(accept)


def register_receiver(app: FastAPI):
    # This machine endpoint authenticates the caller with HMAC; all management/data reads use current_user.
    app.add_api_route("/api/v1/webhooks/incoming/{endpoint_id}", receive, methods=["POST"],
                      tags=["webhooks"], response_model=ReceivedResponse, openapi_extra=EVENT_BODY, summary="Receive a signed external event")
