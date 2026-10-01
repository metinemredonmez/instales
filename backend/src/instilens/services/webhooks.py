"""Generic signed webhook inbox/outbox. Delivery is at-least-once; receivers deduplicate `id`.

Payloads are transport records, never interpreted as disclosures, scores, orders or commands.
The scheduler drains the durable outbox. Every delivery is claimed with a DB compare-and-set.
"""

import hashlib
import hmac
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.enums import WebhookDirection as Direction
from instilens.domain.enums import WebhookStatus as Status
from instilens.domain.models import User, WebhookAttempt, WebhookEndpoint, WebhookMessage
from instilens.services import webhook_transport

MAX_BODY = 65536
MAX_ATTEMPTS = 5
RETRY_SECONDS = (60, 300, 900, 3600)
SIGNATURE_WINDOW = 300


class WebhookError(ValueError):
    pass


class EventEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9._:-]+$", examples=["event-2026-001"])
    type: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z0-9._:-]+$", examples=["portfolio.updated"])
    data: dict = Field(default_factory=dict, examples=[{"reference": "external-123"}])


def now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def signing_secret(endpoint: WebhookEndpoint) -> str:
    # DB-only compromise must not expose working endpoint secrets. Rotation changes secret_seed.
    key = settings.webhook_signing_key or settings.jwt_secret
    context = f"instilens-webhook-v1:{endpoint.id}:{endpoint.secret_seed}".encode()
    return hmac.new(key.encode(), context, hashlib.sha256).hexdigest()


def signature(secret: str, timestamp: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()


def verify(endpoint: WebhookEndpoint, body: bytes, timestamp: str, supplied: str) -> None:
    try:
        valid_time = timestamp.isascii() and timestamp.isdigit() and len(timestamp) <= 12 and abs(int(time.time()) - int(timestamp)) <= SIGNATURE_WINDOW
    except (ValueError, OverflowError):
        valid_time = False
    expected = signature(signing_secret(endpoint), timestamp, body)
    if not valid_time or not hmac.compare_digest(expected.encode(), supplied.encode()):
        raise WebhookError("invalid webhook signature or timestamp")


def endpoint_json(endpoint: WebhookEndpoint, *, reveal: bool = False) -> dict:
    out = {"id": endpoint.id, "name": endpoint.name, "target_url": endpoint.target_url,
           "enabled": endpoint.enabled, "created_at": endpoint.created_at.isoformat(),
           "incoming_path": f"/api/v1/webhooks/incoming/{endpoint.id}"}
    if reveal:
        out["signing_secret"] = signing_secret(endpoint)
    return out


def create_endpoint(session: Session, user: User, name: str, target_url: str | None) -> WebhookEndpoint:
    if not name.strip():
        raise WebhookError("name is required")
    if session.scalar(select(func.count()).select_from(WebhookEndpoint).where(WebhookEndpoint.owner_id == user.id)) >= 10:
        raise WebhookError("at most 10 endpoints per account")
    if target_url:
        webhook_transport.validate_target(target_url)
    endpoint = WebhookEndpoint(id=str(uuid4()), owner_id=user.id, name=name.strip(), target_url=target_url,
                               secret_seed=secrets.token_hex(32), enabled=True)
    session.add(endpoint)
    session.flush()
    return endpoint


def store_message(session: Session, endpoint: WebhookEndpoint, event: EventEnvelope, direction: Direction) -> tuple[WebhookMessage, bool]:
    body = json.dumps(event.model_dump(), ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    encoded = body.encode()
    if len(encoded) > MAX_BODY:
        raise WebhookError("event exceeds 64 KiB")
    digest = hashlib.sha256(encoded).hexdigest()
    existing = session.scalar(select(WebhookMessage).where(
        WebhookMessage.endpoint_id == endpoint.id, WebhookMessage.direction == direction,
        WebhookMessage.external_id == event.id,
    ))
    if existing:
        if existing.body_hash != digest:
            raise WebhookError("event id already exists with different content")
        return existing, True
    if direction == Direction.OUTGOING and not endpoint.target_url:
        raise WebhookError("configure a target URL before sending")
    pending = session.scalar(select(func.count()).select_from(WebhookMessage).where(
        WebhookMessage.endpoint_id == endpoint.id, WebhookMessage.status.in_([Status.PENDING, Status.DELIVERING]),
    ))
    if direction == Direction.OUTGOING and pending >= 500:
        raise WebhookError("outbox full; wait for deliveries before queuing more")
    message = WebhookMessage(endpoint_id=endpoint.id, direction=direction, external_id=event.id,
                             event_type=event.type, body=body, body_hash=digest,
                             target_url=endpoint.target_url if direction == Direction.OUTGOING else None,
                             status=Status.PENDING if direction == Direction.OUTGOING else Status.RECEIVED,
                             next_attempt_at=now())
    try:
        with session.begin_nested():
            session.add(message)
            session.flush()
    except IntegrityError:
        # Concurrent redelivery won the unique (endpoint, direction, external_id) race.
        existing = session.scalar(select(WebhookMessage).where(
            WebhookMessage.endpoint_id == endpoint.id, WebhookMessage.direction == direction,
            WebhookMessage.external_id == event.id,
        ))
        if existing is None:
            raise
        if existing.body_hash != digest:
            raise WebhookError("event id already exists with different content") from None
        return existing, True
    return message, False


def message_json(message: WebhookMessage, *, body: bool = False) -> dict:
    out = {"id": message.id, "endpoint_id": message.endpoint_id, "event_id": message.external_id,
           "event_type": message.event_type, "direction": message.direction, "status": message.status,
           "attempts": message.attempts, "created_at": message.created_at.isoformat(),
           "next_attempt_at": message.next_attempt_at.isoformat() if message.status == Status.PENDING else None}
    if body:
        out["event"] = json.loads(message.body)
    return out


def dispatch(*, engine=None, limit: int = 10) -> int:
    """One bounded worker batch. Never hold a database transaction open during network I/O."""
    from instilens.db.session import get_engine

    engine = engine or get_engine()
    instant = now()
    due = or_(
        (WebhookMessage.status == Status.PENDING) & (WebhookMessage.next_attempt_at <= instant),
        (WebhookMessage.status == Status.DELIVERING) & (WebhookMessage.lease_until <= instant),
    )
    with Session(engine) as session:
        ids = list(session.scalars(select(WebhookMessage.id).join(WebhookEndpoint).join(User).where(
            WebhookMessage.direction == Direction.OUTGOING, due,
            WebhookEndpoint.enabled.is_(True), User.is_active.is_(True),
        ).order_by(WebhookMessage.next_attempt_at, WebhookMessage.id).limit(min(limit, 50))))
    completed = 0
    for message_id in ids:
        lease = uuid4().hex
        with Session(engine) as session:
            claimed = session.execute(update(WebhookMessage).where(WebhookMessage.id == message_id, due).values(
                status=Status.DELIVERING, lease_token=lease, lease_until=now() + timedelta(minutes=3),
            ))
            if claimed.rowcount != 1:
                session.rollback()
                continue
            message = session.get(WebhookMessage, message_id)
            endpoint = session.get(WebhookEndpoint, message.endpoint_id)
            owner = session.get(User, endpoint.owner_id)
            if not endpoint.enabled or not owner.is_active:
                session.rollback()
                continue
            body, target, event_id = message.body.encode(), message.target_url, message.external_id
            secret = signing_secret(endpoint)
            session.commit()
        timestamp = str(int(time.time()))
        code, error = None, None
        try:
            code = webhook_transport.send(target, body, {
                "X-Instilens-Id": event_id, "X-Instilens-Timestamp": timestamp,
                "X-Instilens-Signature": signature(secret, timestamp, body),
            })
        except webhook_transport.TargetError:
            error = "target_not_allowed"
        except Exception:  # noqa: BLE001 — transport errors are recorded without leaking URLs/credentials
            error = "transport_error"
        with Session(engine) as session:
            message = session.scalar(select(WebhookMessage).where(
                WebhookMessage.id == message_id, WebhookMessage.lease_token == lease,
                WebhookMessage.status == Status.DELIVERING,
            ).with_for_update())
            if message is None:
                continue
            message.attempts += 1
            session.add(WebhookAttempt(message_id=message.id, status_code=code, error=error))
            if code is not None and 200 <= code < 300:
                message.status = Status.DELIVERED
            elif message.attempts >= MAX_ATTEMPTS or error == "target_not_allowed" or (code is not None and code < 500 and code not in (408, 429)):
                message.status = Status.FAILED
            else:
                message.status = Status.PENDING
                message.next_attempt_at = now() + timedelta(seconds=RETRY_SECONDS[message.attempts - 1])
            message.lease_token, message.lease_until = None, None
            session.commit()
            completed += 1
    return completed
