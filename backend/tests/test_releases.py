"""Desktop release store: version handout, classification, publish gate, updater manifest, CI upload."""

from fastapi.testclient import TestClient

from instilens.config import settings
from instilens.services import releases


def _client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    return TestClient(app)


def test_version_rules():
    assert releases.bump("1.0.9") == "1.0.10"  # numeric, not string
    assert releases.compare("1.0.10", "1.0.9") == 1 and releases.compare("0.2.0", "0.2.0") == 0
    assert releases.parse("1.0") is None
    assert releases.classify("InstiLens_0.2.0_aarch64.dmg") == ("darwin-aarch64", "INSTALLER")
    assert releases.classify("InstiLens.app.tar.gz") == ("darwin-x86_64", "UPDATE")
    assert releases.classify("InstiLens_0.2.0_x64-setup.exe") == ("windows-x86_64", "UPDATE")
    assert releases.classify("InstiLens_0.2.0_amd64.AppImage") == ("linux-x86_64", "UPDATE")
    assert releases.classify("InstiLens_0.2.0_amd64.deb") == ("linux-x86_64", "INSTALLER")


def test_ci_upload_publish_and_updater(session, tmp_path):
    settings.releases_dir = tmp_path
    settings.release_upload_key = "k" * 32
    c = _client(session)
    h = {"x-release-key": settings.release_upload_key}
    assert c.post("/api/v1/public/desktop/ci/next").status_code == 401  # key required
    v = c.post("/api/v1/public/desktop/ci/next", headers=h).json()["version"]
    assert v == "0.2.0"
    assert c.post("/api/v1/public/desktop/ci/next", headers=h).json()["version"] == v  # same open draft

    up = lambda name, sig=None: c.post("/api/v1/public/desktop/ci/upload", headers=h, data={"version": v, **({"signature": sig} if sig else {})}, files={"file": (name, b"binary")})  # noqa: E731
    assert up("InstiLens.app.tar.gz").status_code == 400  # updater artifact without signature
    assert up("InstiLens_0.2.0_aarch64.app.tar.gz", "SIG").status_code == 200
    assert up("InstiLens_0.2.0_aarch64.dmg").status_code == 200
    assert up("evil/../x.dmg").status_code in (400, 404)

    # nothing is public before publishing
    assert c.get("/api/v1/public/desktop/latest").json() is None
    assert c.get("/api/v1/public/desktop/update/darwin/aarch64/0.1.0").status_code == 204

    # publish via admin
    r = c.post("/api/v1/auth/register", json={"email": "root@example.com", "password": "correct-horse-1", "name": "Root"})
    from instilens.domain.models import User

    u = session.get(User, r.json()["user"]["id"])
    u.role = "ADMIN"
    session.flush()
    ah = {"authorization": f"Bearer {r.json()['access_token']}"}
    rid = c.get("/api/v1/admin/releases", headers=ah).json()["releases"][0]["id"]
    assert c.patch(f"/api/v1/admin/releases/{rid}", json={"status": "PUBLISHED", "notes": "first"}, headers=ah).status_code == 200

    latest = c.get("/api/v1/public/desktop/latest").json()
    assert latest["version"] == v and any(f["kind"] == "INSTALLER" for f in latest["files"])
    m = c.get("/api/v1/public/desktop/update/darwin/aarch64/0.1.0").json()
    assert m["version"] == v and m["platforms"]["darwin-aarch64"]["signature"] == "SIG"
    assert c.get("/api/v1/public/desktop/update/darwin/aarch64/0.2.0").status_code == 204  # up to date
    assert c.get("/api/v1/public/desktop/update/windows/x86_64/0.1.0").status_code == 204  # no windows artifact
    dl = next(f for f in latest["files"] if f["kind"] == "INSTALLER")["url"].split(settings.public_url)[1]
    assert c.get(dl).status_code == 200 and c.get(dl).content == b"binary"

    # published releases cannot be deleted, drafts can; version cannot go backwards
    assert c.delete(f"/api/v1/admin/releases/{rid}", headers=ah).status_code == 400
    assert c.post("/api/v1/public/desktop/ci/upload", headers=h, data={"version": "0.1.9"}, files={"file": ("a.dmg", b"x")}).status_code == 400
    from instilens.api.main import app

    app.dependency_overrides.clear()
