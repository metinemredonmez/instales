"""Organisations (services/org, /api/v1/org): create, invite by e-mail (ORG_INVITE token through the mail path, bound
to its seat), accept with the invited, verified account only, members inheriting the owner's plan, removal and
leaving, the seat cap, and an invitation that gives nothing away about the address."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from instilens.config import settings
from instilens.domain.enums import AuthTokenKind
from instilens.domain.models import AuthToken, Organization, OrgMember, User
from instilens.services import auth, notify


@pytest.fixture(autouse=True)
def _plans_off(monkeypatch):
    monkeypatch.setattr(settings, "plans_enforced", False)


@pytest.fixture
def outbox(monkeypatch):
    box: list[tuple[str, str, str]] = []
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body: box.append((to, subject, body)) or True)
    monkeypatch.setattr(notify, "smtp_configured", lambda: True)
    monkeypatch.setattr(notify, "queue_email", lambda to, subject, body: notify.send_email(to, subject, body))
    return box


@pytest.fixture
def client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


def _user(session, email: str, plan: str = "FREE", lang: str = "tr", verified: bool = True) -> tuple[User, dict]:
    u = auth.register(session, email, "correct-horse-1", email.split("@")[0])
    u.plan, u.plan_source, u.lang, u.email_verified = plan, ("stripe" if plan != "FREE" else None), lang, verified
    session.flush()
    return u, {"authorization": f"Bearer {auth.issue_token(u)}"}


def _token(box) -> str:
    body = box[-1][2]
    assert "/org/accept?token=" in body
    return body.split("/org/accept?token=", 1)[1].split()[0]


def test_create_invite_accept_inherit_and_remove(client, session, outbox):
    owner, ho = _user(session, "owner@example.com", "PRO_PLUS")
    member, hm = _user(session, "member@example.com", lang="en")
    stranger, hs = _user(session, "stranger@example.com")
    assert client.get("/api/v1/org", headers=ho).json() is None
    r = client.post("/api/v1/org", json={"name": "  Tera  Araştırma "}, headers=ho)
    assert r.status_code == 201
    org = r.json()
    assert (org["name"], org["plan"], org["seats"], org["seats_used"], org["is_owner"]) == ("Tera Araştırma", "PRO_PLUS", 10, 1, True)
    assert org["members"][0]["role"] == "OWNER" and org["members"][0]["email"] == "owner@example.com" and org["members"][0]["pending"] is False
    assert client.post("/api/v1/org", json={"name": "Another"}, headers=ho).status_code == 409  # one per account
    assert client.post("/api/v1/org/invite", json={"email": "x@example.com"}, headers=hm).status_code == 404  # no organisation yet

    # Invite: a pending seat, a mailed single-use link in the invitee's language.
    r = client.post("/api/v1/org/invite", json={"email": "Member@Example.com"}, headers=ho)
    assert r.status_code == 201 and r.json()["sent"] is True and r.json()["email"] == "member@example.com"
    assert r.json()["org"]["seats_used"] == 2 and r.json()["org"]["members"][1]["pending"] is True
    assert outbox[-1][0] == "member@example.com" and "invitation to Tera Araştırma" in outbox[-1][1]  # the invitee's account is English
    token = _token(outbox)
    row = session.scalar(select(AuthToken).where(AuthToken.kind == AuthTokenKind.ORG_INVITE))
    assert row.user_id == owner.id and row.used_at is None
    assert client.post("/api/v1/org/invite", json={"email": "member@example.com"}, headers=hm).status_code == 404
    assert client.post("/api/v1/org/invite", json={"email": "not-an-address"}, headers=ho).status_code == 422
    # A second invitation to someone without an account goes out in the owner's language; both links stay live.
    assert client.post("/api/v1/org/invite", json={"email": "new@example.com"}, headers=ho).status_code == 201
    assert outbox[-1][0] == "new@example.com" and "daveti" in outbox[-1][1]
    assert session.scalar(select(AuthToken).where(AuthToken.id == row.id)).used_at is None

    # Accept: only the invited address, only its own link, only once, only verified.
    r = client.post("/api/v1/org/accept", json={"token": token}, headers=hs)
    assert r.status_code == 400 and r.json()["detail"] == "invalid or expired link"  # the stranger's address was not invited
    assert client.post("/api/v1/org/accept", json={"token": "x" * 32}, headers=hm).status_code == 400
    new_token = _token(outbox)  # the second invitee's link, bound to that seat: the member cannot spend it
    r = client.post("/api/v1/org/accept", json={"token": new_token}, headers=hm)
    assert r.status_code == 400 and session.scalar(select(AuthToken).where(AuthToken.subject.is_not(None), AuthToken.id != row.id)).used_at is None
    assert row.subject == str(next(m["id"] for m in client.get("/api/v1/org", headers=ho).json()["members"] if m["email"] == "member@example.com"))
    member.email_verified = False
    session.flush()
    r = client.post("/api/v1/org/accept", json={"token": token}, headers=hm)
    assert r.status_code == 409 and r.json()["detail"] == "verify your e-mail address first"  # the link proves nothing for an unverified account
    member.email_verified = True
    session.flush()
    assert client.get("/api/v1/auth/me", headers=hm).json()["plan"] == "FREE"
    r = client.post("/api/v1/org/accept", json={"token": token}, headers=hm)
    assert r.status_code == 200 and r.json()["is_owner"] is False and r.json()["seats_used"] == 3
    accepted = next(m for m in r.json()["members"] if m["email"] == "member@example.com")
    assert accepted["pending"] is False and accepted["user_id"] == member.id and accepted["name"] == "member"
    assert client.post("/api/v1/org/accept", json={"token": token}, headers=hm).status_code == 400  # burnt
    me = client.get("/api/v1/auth/me", headers=hm).json()
    assert (me["plan"], me["plan_source"], me["own_plan"]) == ("PRO_PLUS", "org", "FREE")  # inherits the owner's plan
    assert me["features"]["portfolio"] is True
    bm = client.get("/api/v1/billing/me", headers=hm).json()
    assert bm["source"] == "org" and bm["org"] == {"id": org["id"], "name": "Tera Araştırma", "plan": "PRO_PLUS", "owner": False}
    assert client.get("/api/v1/org", headers=hm).json()["id"] == org["id"]
    assert client.post("/api/v1/org", json={"name": "Mine"}, headers=hm).status_code == 409  # already a member elsewhere
    assert client.post("/api/v1/org/invite", json={"email": "member@example.com"}, headers=ho).status_code == 409  # already a member
    # Inviting an address that sits in another organisation answers like any other (no account probing); accept is where it stops.
    assert client.post("/api/v1/org", json={"name": "Other"}, headers=hs).status_code == 201
    assert client.post("/api/v1/org/invite", json={"email": "stranger@example.com"}, headers=ho).status_code == 201
    r = client.post("/api/v1/org/accept", json={"token": _token(outbox)}, headers=hs)
    assert r.status_code == 409 and r.json()["detail"] == "you already belong to an organisation"
    members = {m["email"]: m["id"] for m in client.get("/api/v1/org", headers=ho).json()["members"]}
    assert client.delete(f"/api/v1/org/members/{members['stranger@example.com']}", headers=ho).status_code == 204
    assert client.delete("/api/v1/org", headers=hs).status_code == 204

    # The owner's plan is what members get, live: a downgrade reaches them without an event.
    owner.plan = "PRO"
    session.flush()
    assert client.get("/api/v1/auth/me", headers=hm).json()["plan"] == "PRO"
    assert client.get("/api/v1/org", headers=hm).json()["seats"] == 0
    owner.plan = "PRO_PLUS"
    session.flush()

    # Only the owner renames or removes others; a member may leave; the owner's own seat cannot go.
    assert client.patch("/api/v1/org", json={"name": "Renamed"}, headers=hm).status_code == 403
    assert client.patch("/api/v1/org", json={"name": "Renamed"}, headers=ho).json()["name"] == "Renamed"
    members = {m["email"]: m["id"] for m in client.get("/api/v1/org", headers=ho).json()["members"]}
    assert client.delete(f"/api/v1/org/members/{members['owner@example.com']}", headers=ho).status_code == 403
    assert client.delete(f"/api/v1/org/members/{members['new@example.com']}", headers=hm).status_code == 403
    assert client.delete(f"/api/v1/org/members/{members['new@example.com']}", headers=ho).status_code == 204  # a pending invitation dropped
    assert client.delete("/api/v1/org/members/999", headers=ho).status_code == 404
    assert client.delete(f"/api/v1/org/members/{members['member@example.com']}", headers=ho).status_code == 204
    assert client.get("/api/v1/auth/me", headers=hm).json()["plan"] == "FREE" and client.get("/api/v1/org", headers=hm).json() is None
    # Re-invited, the member can leave on their own; the owner can close the organisation.
    client.post("/api/v1/org/invite", json={"email": "member@example.com"}, headers=ho)
    assert client.post("/api/v1/org/accept", json={"token": _token(outbox)}, headers=hm).status_code == 200
    mid = next(m["id"] for m in client.get("/api/v1/org", headers=hm).json()["members"] if m["email"] == "member@example.com")
    assert client.delete(f"/api/v1/org/members/{mid}", headers=hm).status_code == 204
    assert client.delete("/api/v1/org", headers=hm).status_code == 404
    assert client.delete("/api/v1/org", headers=ho).status_code == 204
    assert session.scalar(select(Organization)) is None and session.scalar(select(OrgMember)) is None
    assert client.get("/api/v1/org", headers=ho).json() is None


def test_seat_limit_and_plan_gate(client, session, outbox, monkeypatch):
    monkeypatch.setattr(settings, "plans_enforced", True)
    pro, hp = _user(session, "pro@example.com", "PRO")
    r = client.post("/api/v1/org", json={"name": "Solo"}, headers=hp)
    assert r.status_code == 402 and r.json() == {"detail": "plan_limit", "feature": "org_seats", "plan": "PRO", "limit": 0, "upgrade": "PRO_PLUS"}
    owner, ho = _user(session, "owner@example.com", "PRO_PLUS")
    assert client.post("/api/v1/org", json={"name": "Team"}, headers=ho).status_code == 201
    codes = [client.post("/api/v1/org/invite", json={"email": f"m{i}@example.com"}, headers=ho).status_code for i in range(10)]
    assert codes == [201] * 9 + [402]  # ten seats, the owner holds one
    r = client.post("/api/v1/org/invite", json={"email": "m9@example.com"}, headers=ho)
    assert r.json() == {"detail": "plan_limit", "feature": "org_seats", "plan": "PRO_PLUS", "limit": 10, "upgrade": "PRO_PLUS"}
    assert client.get("/api/v1/org", headers=ho).json()["seats_used"] == 10
    assert client.post("/api/v1/org/invite", json={"email": "m0@example.com"}, headers=ho).status_code == 201  # a pending seat re-mailed, not a new one
    assert len(outbox) == 10
    # A member who joined keeps the seat count; dropping one frees it.
    members = {m["email"]: m["id"] for m in client.get("/api/v1/org", headers=ho).json()["members"]}
    assert client.delete(f"/api/v1/org/members/{members['m8@example.com']}", headers=ho).status_code == 204
    assert client.post("/api/v1/org/invite", json={"email": "m9@example.com"}, headers=ho).status_code == 201
    # With the switch off, the cap does not apply — existing accounts keep working.
    monkeypatch.setattr(settings, "plans_enforced", False)
    assert client.post("/api/v1/org/invite", json={"email": "m10@example.com"}, headers=ho).status_code == 201
    assert client.post("/api/v1/org", json={"name": "Solo"}, headers=hp).status_code == 201
    # ADMIN is never gated.
    monkeypatch.setattr(settings, "plans_enforced", True)
    root, hr = _user(session, "root@example.com")
    root.role = "ADMIN"
    session.flush()
    assert client.post("/api/v1/org", json={"name": "Ops"}, headers=hr).status_code == 201
