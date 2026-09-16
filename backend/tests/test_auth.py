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
    assert c.get(f"/api/v1/screener?token={tok}").status_code == 200  # query token for SSE-style clients
    assert c.get("/api/v1/radar", headers={"authorization": "Bearer nope"}).status_code == 401
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
    codes = [c.post("/api/v1/auth/login", json={"email": "a@b.co", "password": "wrong-password"}).status_code for _ in range(12)]
    assert codes[:10] == [401] * 10 and codes[10:] == [429, 429]
    r = c.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "DENY"
    app.dependency_overrides.clear()
