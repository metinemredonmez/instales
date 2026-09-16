from fastapi.testclient import TestClient

from instilens.services import auth


def _client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    return TestClient(app)


def test_register_login_me_and_protection(session, pipeline_run):
    c = _client(session)
    assert c.get("/api/v1/radar").status_code == 401  # data routes need a user
    assert c.get("/health").status_code == 200

    r = c.post("/api/v1/auth/register", json={"email": "Emre@Example.com", "password": "correct-horse", "name": "Emre"})
    assert r.status_code == 201 and r.json()["user"]["email"] == "emre@example.com"
    assert c.post("/api/v1/auth/register", json={"email": "emre@example.com", "password": "correct-horse"}).status_code == 409
    assert c.post("/api/v1/auth/register", json={"email": "x@example.com", "password": "short"}).status_code == 422

    assert c.post("/api/v1/auth/login", json={"email": "emre@example.com", "password": "wrong-password"}).status_code == 401
    tok = c.post("/api/v1/auth/login", json={"email": "emre@example.com", "password": "correct-horse"}).json()["access_token"]
    h = {"authorization": f"Bearer {tok}"}
    assert c.get("/api/v1/auth/me", headers=h).json()["name"] == "Emre"
    assert c.get("/api/v1/radar", headers=h).status_code == 200
    # Session tokens are refused in the query string; EventSource/<audio> use a 5-minute ticket instead.
    assert c.get(f"/api/v1/screener?token={tok}").status_code == 401
    ticket = c.post("/api/v1/auth/ticket", headers=h).json()["ticket"]
    assert c.get(f"/api/v1/screener?ticket={ticket}").status_code == 401  # ticket is not a session
    assert c.get(f"/api/v1/ai-notes/1/audio?ticket={ticket}").status_code in (404, 503)  # authenticated; note missing / no tts
    assert c.get("/api/v1/ai-notes/1/audio").status_code == 401
    assert c.get("/api/v1/radar", headers={"authorization": "Bearer nope"}).status_code == 401

    # Password change revokes older tokens; the response carries a fresh one.
    assert c.post("/api/v1/auth/password", json={"current_password": "wrong", "new_password": "another-good-one"}, headers=h).status_code == 400
    r = c.post("/api/v1/auth/password", json={"current_password": "correct-horse", "new_password": "another-good-one"}, headers=h)
    assert r.status_code == 200
    assert c.get("/api/v1/auth/me", headers=h).status_code == 401  # old token dead
    h2 = {"authorization": f"Bearer {r.json()['access_token']}"}
    assert c.get("/api/v1/auth/me", headers=h2).status_code == 200
    assert c.post("/api/v1/auth/logout-all", headers=h2).status_code == 200
    assert c.get("/api/v1/auth/me", headers=h2).status_code == 401
    from instilens.api.main import app

    app.dependency_overrides.clear()


def test_password_hash_is_argon2_and_verifies():
    h = auth.hash_password("hunter22!")
    assert h.startswith("$argon2id$") and auth.verify_password("hunter22!", h) and not auth.verify_password("x", h)


def test_auth_rate_limit_and_security_headers(session):
    from instilens.api import deps
    from instilens.api.hardening import reset_rate_limits
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    reset_rate_limits()
    c = TestClient(app)
    # Per-account lockout (8) trips before the per-IP cap (10); both answer 429.
    codes = [c.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "wrong-password"}).status_code for _ in range(12)]
    assert codes[:8] == [401] * 8 and codes[8:] == [429] * 4
    r = c.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    app.dependency_overrides.clear()


def test_password_policy_rules():
    from instilens.config import settings

    settings.breached_password_check = False
    for bad in ("short", "aaaaaaaa", "emre1234x", "InstiLens2027!"):
        try:
            auth.check_password_policy(bad, "emre1234@example.com")
        except auth.AuthError:
            continue
        raise AssertionError(f"{bad!r} should be rejected")
    auth.check_password_policy("correct horse battery staple", "emre1234@example.com")


def test_admin_cannot_remove_last_admin_or_self(session):
    c = _client(session)
    r = c.post("/api/v1/auth/register", json={"email": "root@example.com", "password": "correct-horse-1", "name": "Root"})
    tok = r.json()["access_token"]
    uid = r.json()["user"]["id"]
    from instilens.domain.models import User

    u = session.get(User, uid)
    u.role = "ADMIN"
    session.flush()
    h = {"authorization": f"Bearer {tok}"}
    assert c.patch(f"/api/v1/admin/users/{uid}", json={"role": "USER"}, headers=h).status_code == 400
    assert c.patch(f"/api/v1/admin/users/{uid}", json={"is_active": False}, headers=h).status_code == 400
    assert c.patch(f"/api/v1/admin/users/{uid}", json={"plan": "PRO"}, headers=h).status_code == 200
    events = c.get("/api/v1/admin/audit", headers=h).json()
    assert any(e["kind"] == "admin.user_patch" for e in events) and any(e["kind"] == "auth.registered" for e in events)
    from instilens.api.main import app

    app.dependency_overrides.clear()


def test_runtime_settings_override_and_reset(session):
    from instilens.config import settings
    from instilens.services import runtime_settings as rs

    before = settings.ai_requests_per_hour
    assert rs.set_many(session, {"ai_requests_per_hour": 5, "sec_ciks": "1067983, 12345"}, "root@example.com") == ["ai_requests_per_hour", "sec_ciks"]
    assert settings.ai_requests_per_hour == 5 and settings.sec_ciks == ["1067983", "12345"]
    snap = {x["key"]: x for x in rs.snapshot(session)}
    assert snap["ai_requests_per_hour"]["overridden"] and snap["ai_requests_per_hour"]["default"] == before
    try:
        rs.set_many(session, {"jwt_secret": "x"}, "root@example.com")
    except rs.SettingError:
        pass
    else:
        raise AssertionError("secrets must not be editable")
    try:
        rs.set_many(session, {"account_lockout_attempts": 1}, "root@example.com")
    except rs.SettingError:
        pass
    else:
        raise AssertionError("range must be enforced")
    assert rs.set_many(session, {"ai_requests_per_hour": before}, "root@example.com") == ["ai_requests_per_hour"]  # back to default removes the override
    assert not {x["key"]: x for x in rs.snapshot(session)}["ai_requests_per_hour"]["overridden"]
    settings.sec_ciks = before_ciks = snap["sec_ciks"]["default"]
    rs.set_many(session, {"sec_ciks": before_ciks}, "root@example.com")


def test_lockout_counts_only_failures(session):
    from instilens.api.hardening import reset_rate_limits

    reset_rate_limits()
    c = _client(session)
    c.post("/api/v1/auth/register", json={"email": "ok@example.com", "password": "correct-horse-1", "name": "Ok"})
    for _ in range(9):  # correct logins never count towards the account lock (the per-IP cap of 10/min is separate)
        assert c.post("/api/v1/auth/login", json={"email": "ok@example.com", "password": "correct-horse-1"}).status_code == 200
    reset_rate_limits()  # clear the per-IP counter only; the account counter is empty anyway
    for _ in range(7):
        assert c.post("/api/v1/auth/login", json={"email": "ok@example.com", "password": "wrong-password-x"}).status_code == 401
    assert c.post("/api/v1/auth/login", json={"email": "ok@example.com", "password": "correct-horse-1"}).status_code == 200  # 7 < 8: still allowed, and the counter resets
    reset_rate_limits()
    for _ in range(8):
        c.post("/api/v1/auth/login", json={"email": "ok@example.com", "password": "wrong-password-x"})
    assert c.post("/api/v1/auth/login", json={"email": "ok@example.com", "password": "correct-horse-1"}).status_code == 429  # locked after 8 failures
    from instilens.api.main import app

    app.dependency_overrides.clear()
