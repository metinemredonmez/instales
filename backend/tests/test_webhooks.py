"""Webhook contracts. No real DNS lookup or external request."""
import json
import time
from datetime import timedelta
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from instilens.api import deps
from instilens.api.main import app
from instilens.domain.enums import WebhookDirection as Direction
from instilens.domain.enums import WebhookStatus as Status
from instilens.domain.models import Disclosure, User, WebhookAttempt, WebhookMessage
from instilens.services import auth, webhook_transport, webhooks

PREFIX = "/api/v1/webhooks"
EVENT = {"id": "ext-1", "type": "custom.event", "data": {"value": "12.45"}}


@pytest.fixture
def client(session):
    def db():
        try:
            yield session
            session.commit()
        except BaseException:
            session.rollback()
            raise
    app.dependency_overrides[deps.get_session] = db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def users(session):
    rows = [User(email=f"webhook{i}@example.com", name=f"User {i}", password_hash="unused") for i in (1, 2)]
    session.add_all(rows)
    session.commit()
    return rows


def headers(user):
    return {"Authorization": f"Bearer {auth.issue_token(user)}"}


def endpoint(client, user, target="https://example.com/hook"):
    response = client.post(PREFIX + "/endpoints", headers=headers(user), json={"name": "Integration", "target_url": target})
    assert response.status_code == 201, response.text
    return response.json()


def signed(secret, body, timestamp=None):
    timestamp = timestamp or str(int(time.time()))
    return {"Content-Type": "application/json", "X-Instilens-Timestamp": timestamp,
            "X-Instilens-Signature": webhooks.signature(secret, timestamp, body)}


def test_schema_and_owner_isolation(client, users):
    assert client.get("/api/v1/openapi.json").status_code == 401
    response = client.get("/api/v1/openapi.json", headers=headers(users[0]))
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    spec = response.json()
    assert spec["components"]["securitySchemes"]["BearerAuth"]["scheme"] == "bearer"
    assert spec["paths"][PREFIX + "/endpoints"]["get"]["security"] == [{"BearerAuth": []}]
    receiver = spec["paths"][PREFIX + "/incoming/{endpoint_id}"]["post"]
    assert receiver["security"] == [{"WebhookSignature": []}]
    assert "id" in receiver["requestBody"]["content"]["application/json"]["schema"]["properties"]
    assert client.get(PREFIX + "/endpoints").status_code == 401
    ep = endpoint(client, users[0])
    assert "signing_secret" not in client.get(PREFIX + "/endpoints", headers=headers(users[0])).json()[0]
    assert client.get(PREFIX + "/endpoints", headers=headers(users[1])).json() == []
    root = PREFIX + "/endpoints/" + ep["id"]
    for method, path, body in [("get", "/messages", None), ("post", "/rotate", None),
                               ("post", "/send", EVENT), ("post", "/test", None), ("patch", "", {"enabled": False})]:
        assert client.request(method, root + path, headers=headers(users[1]), json=body).status_code == 404


def test_inbox_signature_replay_rotation_pause(client, users, session):
    ep = endpoint(client, users[0], None)
    body = json.dumps(EVENT).encode()
    url = ep["incoming_path"]
    assert client.post(url, content=body, headers={"Content-Type": "application/json"}).status_code == 401
    assert client.post(url, content=body + b" ", headers=signed(ep["signing_secret"], body)).status_code == 401
    assert client.post(url, content=body, headers=signed(ep["signing_secret"], body, str(int(time.time()) - 301))).status_code == 401
    result = client.post(url, content=body, headers=signed(ep["signing_secret"], body)).json()
    assert result["received"] and not result["duplicate"]
    assert client.post(url, content=body, headers=signed(ep["signing_secret"], body)).json() == {**result, "duplicate": True}
    changed = json.dumps({**EVENT, "data": {"value": "different"}}).encode()
    assert client.post(url, content=changed, headers=signed(ep["signing_secret"], changed)).status_code == 409
    detail = client.get(f"{PREFIX}/messages/{result['id']}", headers=headers(users[0])).json()
    assert detail["event"] == EVENT and detail["status"] == "received"
    assert client.get(f"{PREFIX}/messages/{result['id']}", headers=headers(users[1])).status_code == 404
    assert client.post(f"{PREFIX}/messages/{result['id']}/retry", headers=headers(users[0])).status_code == 409
    assert session.scalar(select(func.count()).select_from(Disclosure)) == 0
    rotated = client.post(f"{PREFIX}/endpoints/{ep['id']}/rotate", headers=headers(users[0])).json()["signing_secret"]
    assert rotated != ep["signing_secret"]
    assert client.post(url, content=body, headers=signed(ep["signing_secret"], body)).status_code == 401
    assert client.post(url, content=body, headers=signed(rotated, body)).status_code == 200
    client.patch(f"{PREFIX}/endpoints/{ep['id']}", headers=headers(users[0]), json={"enabled": False})
    assert client.post(url, content=body, headers=signed(rotated, body)).status_code == 404


@pytest.mark.parametrize("body,code", [(b"x" * 65537, 413), (b'{"id":"e","type":"t","data":{"n":NaN}}', 422),
    (b'{"id":"e","type":"t","data":[]}', 422), (b'{"id":"e","type":"t","data":{},"command":"x"}', 422)])
def test_body_limits(client, users, body, code):
    ep = endpoint(client, users[0])
    assert client.post(ep["incoming_path"], content=body, headers=signed(ep["signing_secret"], body)).status_code == code
    assert client.post(f"{PREFIX}/endpoints/{ep['id']}/send", content=body,
                       headers={**headers(users[0]), "Content-Type": "application/json"}).status_code == code


def test_content_type_inactive_owner_and_pagination(client, users, session):
    ep = endpoint(client, users[0])
    body = json.dumps(EVENT).encode()
    assert client.post(ep["incoming_path"], content=body).status_code == 415
    assert client.post(ep["incoming_path"], content=body, headers={**signed(ep["signing_secret"], body), "Content-Encoding": "gzip"}).status_code == 415
    path = f"{PREFIX}/endpoints/{ep['id']}"
    for i in range(3):
        assert client.post(path + "/send", headers=headers(users[0]), json={**EVENT, "id": str(i)}).status_code == 202
    page = client.get(path + "/messages?limit=2", headers=headers(users[0])).json()
    rest = client.get(path + f"/messages?before={page[-1]['id']}", headers=headers(users[0])).json()
    assert len(page) == 2 and len(rest) == 1 and rest[0]["id"] < page[-1]["id"]
    users[0].is_active = False
    session.commit()
    assert client.post(ep["incoming_path"], content=body, headers=signed(ep["signing_secret"], body)).status_code == 404
    assert webhooks.dispatch(engine=session.get_bind()) == 0


@pytest.mark.parametrize("target", ["http://example.com", "https://127.0.0.1", "https://224.0.0.1", "https://[64:ff9b::7f00:1]", "https://[::1]", "https://10.0.0.1", "https://169.254.169.254", "https://localhost", "https://foo.local", "https://user:pass@example.com", "https://example.com:8443", "https://example.com/#fragment", "https://example.com/\r\nx"])
def test_unsafe_targets_rejected(target):
    with pytest.raises(webhook_transport.TargetError):
        webhook_transport.validate_target(target)


def test_dns_rebinding_and_redirect_not_followed(monkeypatch):
    dns = Mock(return_value=[(0, 0, 0, "", ("1.1.1.1", 443)), (0, 0, 0, "", ("127.0.0.1", 443))])
    monkeypatch.setattr(webhook_transport.socket, "getaddrinfo", dns)
    with pytest.raises(webhook_transport.TargetError):
        webhook_transport.public_addresses("example.com")
    dns.return_value = [(0, 0, 0, "", ("1.1.1.1", 443))]
    connection = Mock()
    connection.getresponse.return_value.status = 302
    constructor = Mock(return_value=connection)
    monkeypatch.setattr(webhook_transport, "_PinnedHTTPS", constructor)
    assert webhook_transport.send("https://example.com/hook?x=1", b"{}", {}) == 302
    constructor.assert_called_once_with("example.com", "1.1.1.1")
    assert connection.request.call_args.args == ("POST", "/hook?x=1")
    connection.close.assert_called_once()
    assert connection.request.call_count == 1


def test_tls_uses_original_hostname(monkeypatch):
    sock, context = Mock(), Mock()
    create = Mock(return_value=sock)
    monkeypatch.setattr(webhook_transport.socket, "create_connection", create)
    monkeypatch.setattr(webhook_transport.ssl, "create_default_context", Mock(return_value=context))
    conn = webhook_transport._PinnedHTTPS("example.com", "1.1.1.1")
    conn.connect()
    create.assert_called_once_with(("1.1.1.1", 443), timeout=10)
    context.wrap_socket.assert_called_once_with(sock, server_hostname="example.com")


def test_outbox_retries_signing_and_history(client, users, session, monkeypatch):
    ep = endpoint(client, users[0])
    path = f"{PREFIX}/endpoints/{ep['id']}/send"
    response = client.post(path, headers=headers(users[0]), json=EVENT)
    assert response.status_code == 202
    result = response.json()
    assert result["status"] == "pending"
    assert client.post(path, headers=headers(users[0]), json=EVENT).json()["duplicate"]
    assert client.post(path, headers=headers(users[0]), json={**EVENT, "data": {"different": True}}).status_code == 409
    send = Mock(return_value=503)
    monkeypatch.setattr(webhook_transport, "send", send)
    engine = session.get_bind()
    for n in range(1, 6):
        assert webhooks.dispatch(engine=engine) == 1
        session.expire_all()
        row = session.get(WebhookMessage, result["id"])
        assert row.attempts == n and row.status == (Status.FAILED if n == 5 else Status.PENDING)
        if n < 5:
            assert webhooks.dispatch(engine=engine) == 0
            row.next_attempt_at = webhooks.now() - timedelta(seconds=1)
            session.commit()
    sent_url, body, h = send.call_args.args
    assert sent_url == ep["target_url"] and json.loads(body) == EVENT
    assert h["X-Instilens-Signature"] == webhooks.signature(ep["signing_secret"], h["X-Instilens-Timestamp"], body)
    assert h["X-Instilens-Id"] == EVENT["id"]
    retry = f"{PREFIX}/messages/{result['id']}/retry"
    assert client.post(retry, headers=headers(users[1])).status_code == 404
    assert client.post(retry, headers=headers(users[0])).status_code == 202
    assert client.post(retry, headers=headers(users[0])).status_code == 409
    send.return_value = 204
    assert webhooks.dispatch(engine=engine) == 1
    detail = client.get(f"{PREFIX}/messages/{result['id']}", headers=headers(users[0])).json()
    assert detail["status"] == "delivered" and detail["attempts"] == 6
    assert len(detail["deliveries"]) == 6
    assert webhooks.dispatch(engine=engine) == 0


@pytest.mark.parametrize("result,status,error", [(400, Status.FAILED, None), (302, Status.FAILED, None), (429, Status.PENDING, None), (408, Status.PENDING, None), (201, Status.DELIVERED, None), (OSError("secret remote message"), Status.PENDING, "transport_error"), (webhook_transport.TargetError("private"), Status.FAILED, "target_not_allowed")])
def test_worker_status_and_lease(session, users, monkeypatch, result, status, error):
    ep = webhooks.create_endpoint(session, users[0], "target", "https://example.com/hook")
    msg, _ = webhooks.store_message(session, ep, webhooks.EventEnvelope(**EVENT), Direction.OUTGOING)
    msg.status = Status.DELIVERING
    msg.lease_token = "abandoned"
    msg.lease_until = webhooks.now() + timedelta(minutes=1)
    session.commit()
    send = Mock(side_effect=result) if isinstance(result, Exception) else Mock(return_value=result)
    monkeypatch.setattr(webhook_transport, "send", send)
    assert webhooks.dispatch(engine=session.get_bind()) == 0
    msg.lease_until = webhooks.now() - timedelta(seconds=1)
    ep.enabled = False
    session.commit()
    assert webhooks.dispatch(engine=session.get_bind()) == 0
    ep.enabled = True
    session.commit()
    assert webhooks.dispatch(engine=session.get_bind()) == 1
    session.expire_all()
    assert msg.status == status and msg.lease_token is None and msg.attempts == 1
    attempt = session.scalar(select(WebhookAttempt).where(WebhookAttempt.message_id == msg.id))
    assert attempt.error == error


def test_incoming_only_and_paused_queue(client, users):
    ep = endpoint(client, users[0], None)
    path = f"{PREFIX}/endpoints/{ep['id']}"
    assert client.post(path + "/send", headers=headers(users[0]), json=EVENT).status_code == 409
    client.patch(path, headers=headers(users[0]), json={"enabled": False})
    assert client.post(path + "/test", headers=headers(users[0])).status_code == 409
