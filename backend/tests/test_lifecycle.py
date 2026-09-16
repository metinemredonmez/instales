"""Account lifecycle (reset / verify / MFA), shared counters and the pipeline lock, brief delivery hygiene,
AI budget + failure mapping. Network-free: mail and the model are monkeypatched."""

from datetime import UTC, date, datetime, timedelta

import pyotp
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from instilens.api import hardening
from instilens.domain.models import AiNote, AuthToken, PushSubscription, User
from instilens.services import auth, notify
from tests.conftest import AS_OF


@pytest.fixture
def client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def outbox(monkeypatch):
    """Capture every e-mail instead of talking SMTP. queue_email is drained inline so assertions do not race the
    background sender; the real out-of-band worker has its own test (test_auth_mail_is_sent_out_of_band)."""
    box: list[tuple[str, str, str]] = []
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body: box.append((to, subject, body)) or True)
    monkeypatch.setattr(notify, "smtp_configured", lambda: True)
    monkeypatch.setattr(notify, "queue_email", lambda to, subject, body: notify.send_email(to, subject, body))
    return box


def _register(client, email="emre@example.com", password="correct-horse"):
    r = client.post("/api/v1/auth/register", json={"email": email, "password": password, "name": "Emre"})
    assert r.status_code == 201
    return r.json()["access_token"]


def _token_from(box, path):
    body = box[-1][2]
    marker = f"{path}?token="
    assert marker in body
    return body.split(marker, 1)[1].split()[0]


# ---------------------------------------------------------------- password reset & e-mail verification
def test_public_config_and_verification_flow(client, session, outbox):
    assert client.get("/api/v1/auth/config").json() == {"allow_registration": True}
    tok = _register(client)
    h = {"authorization": f"Bearer {tok}"}
    me = client.get("/api/v1/auth/me", headers=h).json()
    assert me["email_verified"] is False and me["mfa_enabled"] is False  # login is not blocked on verification
    assert len(outbox) == 1 and outbox[0][0] == "emre@example.com"
    vtoken = _token_from(outbox, "/verify")
    assert client.post("/api/v1/auth/verify", json={"token": vtoken}).status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).json()["email_verified"] is True
    assert client.post("/api/v1/auth/verify", json={"token": vtoken}).status_code == 400  # single use
    row = session.query(AuthToken).filter_by(kind="VERIFY").one()
    assert row.used_at is not None and len(row.token_hash) == 64 and vtoken not in row.token_hash  # hashed at rest


def test_forgot_and_reset_password(client, session, outbox):
    old = _register(client)
    outbox.clear()
    # Unknown address: same 200, no mail (no enumeration).
    assert client.post("/api/v1/auth/forgot", json={"email": "nobody@example.com"}).status_code == 200 and not outbox
    assert client.post("/api/v1/auth/forgot", json={"email": "Emre@Example.com"}).status_code == 200
    assert len(outbox) == 1 and "/reset?token=" in outbox[0][2]
    rtoken = _token_from(outbox, "/reset")
    # Policy applies to the new password.
    assert client.post("/api/v1/auth/reset", json={"token": rtoken, "new_password": "aaaaaaaa"}).status_code == 400
    r = client.post("/api/v1/auth/reset", json={"token": rtoken, "new_password": "battery staple horse"})
    assert r.status_code == 200
    assert client.get("/api/v1/auth/me", headers={"authorization": f"Bearer {old}"}).status_code == 401  # sessions revoked
    assert client.get("/api/v1/auth/me", headers={"authorization": f"Bearer {r.json()['access_token']}"}).status_code == 200
    assert client.post("/api/v1/auth/reset", json={"token": rtoken, "new_password": "battery staple horse2"}).status_code == 400  # burnt
    assert client.post("/api/v1/auth/login", json={"email": "emre@example.com", "password": "battery staple horse"}).status_code == 200
    kinds = [e.kind for e in session.query(__import__("instilens.domain.models", fromlist=["AuditEvent"]).AuditEvent)]
    assert "auth.reset_requested" in kinds and "auth.password_reset" in kinds


def test_reset_token_expires(session):
    user = auth.register(session, "t@example.com", "correct-horse", "T")
    token = auth.issue_email_token(session, user, "RESET", auth.RESET_TTL)
    row = session.query(AuthToken).one()
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    session.flush()
    with pytest.raises(auth.AuthError):
        auth.reset_password(session, token, "battery staple horse")
    # A fresh token supersedes the old one even if the old one had not expired.
    t1 = auth.issue_email_token(session, user, "RESET", auth.RESET_TTL)
    t2 = auth.issue_email_token(session, user, "RESET", auth.RESET_TTL)
    with pytest.raises(auth.AuthError):
        auth.consume_email_token(session, "RESET", t1)
    assert auth.consume_email_token(session, "RESET", t2).id == user.id


# ---------------------------------------------------------------- TOTP MFA
def test_mfa_enrol_login_and_disable(client, session, outbox):
    tok = _register(client)
    h = {"authorization": f"Bearer {tok}"}
    assert client.post("/api/v1/auth/mfa/enable", json={"code": "000000"}, headers=h).status_code == 400  # setup first
    setup = client.post("/api/v1/auth/mfa/setup", headers=h).json()
    assert setup["otpauth_uri"].startswith("otpauth://totp/InstiLens:emre%40example.com?") and setup["secret"] in setup["otpauth_uri"]
    assert client.get("/api/v1/auth/me", headers=h).json()["mfa_enabled"] is False  # pending until a code confirms
    assert client.post("/api/v1/auth/mfa/enable", json={"code": "000000"}, headers=h).status_code == 400
    code = pyotp.TOTP(setup["secret"]).now()
    assert client.post("/api/v1/auth/mfa/enable", json={"code": code}, headers=h).status_code == 200
    assert client.get("/api/v1/auth/me", headers=h).json()["mfa_enabled"] is True

    # Login now stops at the second factor; the mfa token is not a session.
    r = client.post("/api/v1/auth/login", json={"email": "emre@example.com", "password": "correct-horse"})
    assert r.status_code == 200 and r.json()["mfa_required"] is True and "access_token" not in r.json()
    mfa_token = r.json()["mfa_token"]
    assert client.get("/api/v1/auth/me", headers={"authorization": f"Bearer {mfa_token}"}).status_code == 401
    assert client.post("/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": "123456"}).status_code == 401
    r2 = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": pyotp.TOTP(setup["secret"]).now()})
    assert r2.status_code == 200
    h2 = {"authorization": f"Bearer {r2.json()['access_token']}"}
    assert client.get("/api/v1/auth/me", headers=h2).status_code == 200
    assert client.post("/api/v1/auth/mfa/verify", json={"mfa_token": tok, "code": "123456"}).status_code == 401  # session token refused here

    assert client.post("/api/v1/auth/mfa/disable", json={"code": "000000"}, headers=h2).status_code == 400
    assert client.post("/api/v1/auth/mfa/disable", json={"code": pyotp.TOTP(setup["secret"]).now()}, headers=h2).status_code == 200
    assert session.get(User, r2.json()["user"]["id"]).totp_secret is None
    assert "access_token" in client.post("/api/v1/auth/login", json={"email": "emre@example.com", "password": "correct-horse"}).json()


def test_mfa_wrong_codes_lock_the_account(session):
    from instilens.config import settings

    user = auth.register(session, "m@example.com", "correct-horse", "M")
    user.totp_secret = pyotp.random_base32()
    session.flush()
    mfa_token = auth.issue_mfa_token(user)
    for _ in range(settings.account_lockout_attempts):
        with pytest.raises(auth.AuthError):
            auth.mfa_verify(session, mfa_token, "000000")
    with pytest.raises(auth.LockedOut):
        auth.mfa_verify(session, mfa_token, pyotp.TOTP(user.totp_secret).now())


# ---------------------------------------------------------------- shared counters + pipeline lock
def test_rate_hits_live_in_the_database():
    from instilens.domain.models import RateHit

    assert hardening.hit("k", 2, 60) and hardening.hit("k", 2, 60) and not hardening.hit("k", 2, 60)
    assert hardening.failures("k", 60) == 2
    with hardening._store() as s:
        assert s.query(RateHit).filter_by(key="k").count() == 2
        for row in s.query(RateHit).filter_by(key="k"):
            row.ts = datetime.now(UTC) - timedelta(seconds=120)  # both hits age out of the window
        s.commit()
    assert hardening.failures("k", 60) == 0 and hardening.hit("k", 2, 60)
    hardening.reset_key("k")
    assert hardening.failures("k", 60) == 0
    hardening.hit("other", 5, 60)
    hardening.reset_rate_limits()
    with hardening._store() as s:
        assert s.query(RateHit).count() == 0


def test_pipeline_lock_is_exclusive_and_recovers():
    first = hardening.acquire_pipeline_lock("a@example.com")
    assert first is not None
    assert hardening.acquire_pipeline_lock("b@example.com") is None  # second admin gets nothing
    st = hardening.pipeline_run_status()
    assert st["running"] is True and st["started_by"] == "a@example.com"
    hardening.finish_pipeline_run(first, result={"parsed": 1})
    st = hardening.pipeline_run_status()
    assert st["running"] is False and st["result"] == {"parsed": 1} and st["finished_at"]
    second = hardening.acquire_pipeline_lock("b@example.com")
    assert second is not None and second != first
    hardening.finish_pipeline_run(second, error="boom")
    assert hardening.pipeline_run_status()["error"] == "boom"
    # A worker that died mid-run leaves a stale RUNNING row; it is taken over after PIPELINE_STALE_AFTER.
    third = hardening.acquire_pipeline_lock("c@example.com")
    with hardening._store() as s:
        from instilens.domain.models import PipelineRun

        s.get(PipelineRun, third).started_at = datetime.now(UTC) - hardening.PIPELINE_STALE_AFTER - timedelta(minutes=1)
        s.commit()
    assert hardening.acquire_pipeline_lock("d@example.com") is not None


def test_admin_pipeline_run_endpoint_uses_the_lock(client, session, monkeypatch):
    from instilens.api.routes import admin as admin_routes

    tok = _register(client)
    u = session.query(User).one()
    u.role = "ADMIN"
    session.flush()
    h = {"authorization": f"Bearer {tok}"}
    monkeypatch.setattr(admin_routes, "_run_pipeline_bg", lambda run_id: None)  # thread does nothing; lock stays held
    assert client.post("/api/v1/admin/pipeline/run", headers=h).json()["started"] is True
    r = client.post("/api/v1/admin/pipeline/run", headers=h).json()
    assert r["started"] is False and r["running"] is True
    assert client.get("/api/v1/admin/pipeline/status", headers=h).json()["running"] is True


# ---------------------------------------------------------------- push + brief delivery hygiene
def test_push_subscribe_cannot_take_over_another_users_endpoint(client, session):
    t1 = _register(client, "one@example.com")
    t2 = _register(client, "two@example.com")
    sub = {"endpoint": "https://push.example/abc", "keys": {"p256dh": "k", "auth": "a"}}
    assert client.post("/api/v1/push/subscribe", json=sub, headers={"authorization": f"Bearer {t1}"}).status_code == 201
    assert client.post("/api/v1/push/subscribe", json=sub, headers={"authorization": f"Bearer {t1}"}).status_code == 201  # own re-register ok
    assert client.post("/api/v1/push/subscribe", json=sub, headers={"authorization": f"Bearer {t2}"}).status_code == 409
    assert session.query(PushSubscription).count() == 1


def test_brief_delivery_is_idempotent_and_per_market(session, monkeypatch, outbox):
    tr = auth.register(session, "tr@example.com", "correct-horse", "TR")
    both = auth.register(session, "both@example.com", "correct-horse", "Both")
    off = auth.register(session, "off@example.com", "correct-horse", "Off")
    for u in (tr, both):
        u.notify_email = True
    both.brief_markets, off.notify_brief = ["TR", "US"], False
    monkeypatch.setattr(notify, "send_push", lambda *a, **k: 0)
    monkeypatch.setattr(notify, "send_onesignal", lambda *a, **k: False)
    note_tr = AiNote(kind="DAILY_BRIEF", market_code="TR", subject="market", as_of=AS_OF, lang="tr", content="Fonlar net alıcıydı.", data={"watch": []}, model="test")
    note_us = AiNote(kind="DAILY_BRIEF", market_code="US", subject="market", as_of=AS_OF, lang="tr", content="Funds were net buyers.", data={"watch": []}, model="test")
    session.add_all([note_tr, note_us])
    session.flush()
    assert notify.deliver_brief(session, note_tr) == 2 and sorted(m[0] for m in outbox) == ["both@example.com", "tr@example.com"]
    assert notify.deliver_brief(session, note_tr) == 0 and len(outbox) == 2  # re-run: nothing re-sent
    assert notify.deliver_brief(session, note_us) == 1 and outbox[-1][0] == "both@example.com"
    assert notify.deliver_brief(session, note_us) == 0 and len(outbox) == 3


def test_settings_brief_markets(client, session):
    tok = _register(client)
    h = {"authorization": f"Bearer {tok}"}
    assert client.get("/api/v1/me/settings", headers=h).json()["brief_markets"] == ["TR"]
    assert client.put("/api/v1/me/settings", json={"brief_markets": ["us", "TR"]}, headers=h).status_code == 200
    assert client.get("/api/v1/me/settings", headers=h).json()["brief_markets"] == ["TR", "US"]
    assert client.put("/api/v1/me/settings", json={"brief_markets": ["XX"]}, headers=h).status_code == 400
    assert client.put("/api/v1/me/settings", json={"brief_markets": []}, headers=h).status_code == 400


def test_push_prefers_onesignal_over_vapid(session, monkeypatch):
    from instilens.config import settings

    calls: list[str] = []
    monkeypatch.setattr(notify, "send_onesignal", lambda *a, **k: calls.append("onesignal") or True)
    monkeypatch.setattr(notify, "send_push", lambda *a, **k: calls.append("push") or 1)
    monkeypatch.setattr(settings, "onesignal_app_id", "app")
    monkeypatch.setattr(settings, "onesignal_rest_api_key", "key")
    assert notify.push_user(session, "1", "t", "b", "l") == "onesignal" and calls == ["onesignal"]  # VAPID skipped: no double buzz
    calls.clear()
    monkeypatch.setattr(settings, "onesignal_app_id", None)
    assert notify.push_user(session, "1", "t", "b", "l") == "push" and calls == ["push"]


# ---------------------------------------------------------------- AI budget + failure mapping
def test_ai_budget_charged_only_on_real_model_calls(session, pipeline_run, monkeypatch):
    from instilens.ai import assess

    charged: list[int] = []
    monkeypatch.setattr(assess, "_client", lambda: object())  # "AI configured"

    def parse_overrun(client, prompt, data):
        assess.Note(text="x" * 5000)  # the model exceeded max_length → pydantic.ValidationError

    monkeypatch.setattr(assess, "_parse", parse_overrun)
    session.add(AiNote(kind="STOCK_ASSESSMENT", market_code="TR", subject="ASELS", as_of=date.today(), lang="tr", content="cached", data={}, model="test"))
    session.flush()
    note = assess.stock_assessment(session, "TR", "ASELS", budget=lambda: charged.append(1))
    assert note.content == "cached" and charged == []  # cache hit: no charge, no model call
    with pytest.raises(assess.AiUnavailable):  # first-time generation of another symbol: charged, then the overrun maps to AiUnavailable
        assess.stock_assessment(session, "TR", "THYAO", budget=lambda: charged.append(1))
    assert charged == [1]
    with pytest.raises(ValidationError):
        parse_overrun(None, "", {})


def test_ai_validation_error_answers_503(client, session, pipeline_run, monkeypatch):
    from instilens.ai import assess

    tok = _register(client)
    h = {"authorization": f"Bearer {tok}"}
    monkeypatch.setattr(assess, "_client", lambda: object())
    monkeypatch.setattr(assess, "_parse", lambda client, prompt, data: assess.Note(text="x" * 5000))
    r = client.get("/api/v1/stocks/ASELS/ai", headers=h)
    assert r.status_code == 503 and "ai unavailable" in r.json()["detail"]
    monkeypatch.setattr(assess, "_parse", lambda client, prompt, data: (_ for _ in ()).throw(assess.AiUnavailable("quota")))
    assert client.get("/api/v1/brief", headers=h).status_code == 503


def test_news_enrich_maps_schema_overrun_to_unavailable(session, pipeline_run, monkeypatch):
    from instilens.ai import news_enrich
    from instilens.config import settings
    from instilens.domain.models import NewsItem

    session.add(NewsItem(market_code="TR", source="t", title="Başlık", url="https://n.example/1", published_at=datetime.now(UTC)))
    session.flush()
    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")

    class _Messages:
        def parse(self, **kw):
            news_enrich.HeadlineTag(id=1, summary_tr="x" * 500)

    class _Client:
        def __init__(self, api_key):
            self.messages = _Messages()

    monkeypatch.setattr(news_enrich.anthropic, "Anthropic", _Client)
    with pytest.raises(news_enrich.AiUnavailable):
        news_enrich.enrich(session, "TR")


def test_cheap_models_for_tagging_and_extraction():
    from instilens.config import Settings

    fresh = Settings(_env_file=None)
    assert fresh.ai_news_model == "claude-haiku-4-5" and fresh.ai_extract_model == "claude-haiku-4-5"
    assert fresh.ai_model == "claude-opus-5"


def test_cheap_model_calls_use_the_haiku_request_shape(session, monkeypatch):
    """Haiku 4.5 rejects adaptive thinking and output_config.effort (both are 4.6+ parameters), so the two cheap
    calls must send an explicit thinking budget instead. Without this the request 400s on every real filing/headline."""
    from instilens.ai import filing_extract, news_enrich
    from instilens.config import settings
    from instilens.domain.models import NewsItem

    seen: list[dict] = []

    class _Messages:
        def parse(self, **kw):
            seen.append(kw)
            raise AssertionError("stop after capturing the request")

    class _Client:
        def __init__(self, api_key):
            self.messages = _Messages()

    monkeypatch.setattr(settings, "anthropic_api_key", "sk-test")
    monkeypatch.setattr(news_enrich.anthropic, "Anthropic", _Client)
    monkeypatch.setattr(filing_extract.anthropic, "Anthropic", _Client)
    session.add(NewsItem(market_code="TR", source="t", title="Başlık", url="https://n.example/2", published_at=datetime.now(UTC)))
    session.flush()
    for call in (lambda: news_enrich.enrich(session, "TR"), lambda: filing_extract.extract_transactions("x" * 60)):
        with pytest.raises(AssertionError):
            call()
    assert len(seen) == 2
    for kw in seen:
        assert kw["model"] == "claude-haiku-4-5"
        assert kw["thinking"] == {"type": "enabled", "budget_tokens": 2000}
        assert "output_config" not in kw  # effort errors on Haiku 4.5
        assert kw["thinking"]["budget_tokens"] < kw["max_tokens"] and kw["thinking"]["budget_tokens"] >= 1024


# ---------------------------------------------------------------- the defects the review found
def test_reset_does_not_bypass_mfa(client, session, outbox):
    """Mailbox access must not hand out an admin session on an account that carries a second factor."""
    tok = _register(client)
    h = {"authorization": f"Bearer {tok}"}
    secret = client.post("/api/v1/auth/mfa/setup", headers=h).json()["secret"]
    assert client.post("/api/v1/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()}, headers=h).status_code == 200
    session.query(User).one().role = "ADMIN"
    session.flush()
    outbox.clear()
    assert client.post("/api/v1/auth/forgot", json={"email": "emre@example.com"}).status_code == 200
    r = client.post("/api/v1/auth/reset", json={"token": _token_from(outbox, "/reset"), "new_password": "battery staple horse"})
    assert r.status_code == 200
    body = r.json()
    assert body["mfa_required"] is True and "access_token" not in body  # the challenge, not a session
    r2 = client.post("/api/v1/auth/mfa/verify", json={"mfa_token": body["mfa_token"], "code": pyotp.TOTP(secret).now()})
    assert r2.status_code == 200 and r2.json()["user"]["role"] == "ADMIN"


def test_mfa_token_is_single_use(client, session):
    tok = _register(client)
    h = {"authorization": f"Bearer {tok}"}
    secret = client.post("/api/v1/auth/mfa/setup", headers=h).json()["secret"]
    client.post("/api/v1/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()}, headers=h)
    mfa_token = client.post("/api/v1/auth/login", json={"email": "emre@example.com", "password": "correct-horse"}).json()["mfa_token"]
    assert client.post("/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()}).status_code == 200
    # Same token, a fresh valid code: replaying it must not mint a second session.
    assert client.post("/api/v1/auth/mfa/verify", json={"mfa_token": mfa_token, "code": pyotp.TOTP(secret).now()}).status_code == 401


def test_password_alone_is_not_audited_as_a_login(client, session):
    from instilens.domain.models import AuditEvent

    tok = _register(client)
    h = {"authorization": f"Bearer {tok}"}
    secret = client.post("/api/v1/auth/mfa/setup", headers=h).json()["secret"]
    client.post("/api/v1/auth/mfa/enable", json={"code": pyotp.TOTP(secret).now()}, headers=h)
    session.query(User).one().last_login_at = None
    session.flush()
    client.post("/api/v1/auth/login", json={"email": "emre@example.com", "password": "correct-horse"})
    kinds = [e.kind for e in session.query(AuditEvent)]
    assert "auth.login_password_ok" in kinds and "auth.login_ok" not in kinds
    assert session.query(User).one().last_login_at is None  # the login is not finished yet


def test_counter_store_fails_closed_when_the_database_errors(session, monkeypatch):
    """A transient database error used to drop the caller back to an empty per-process deque, which silently
    dissolved an active lockout. `failures` must now report the key as locked and `hit` must deny."""
    from sqlalchemy.exc import OperationalError

    from instilens.config import settings

    user = auth.register(session, "lock@example.com", "correct-horse", "L")
    for _ in range(settings.account_lockout_attempts):
        with pytest.raises(auth.AuthError):
            auth.authenticate(session, user.email, "wrong-password-here")
    with pytest.raises(auth.LockedOut):
        auth.authenticate(session, user.email, "correct-horse")

    def _broken():
        raise OperationalError("SELECT 1", {}, Exception("database is locked"))

    monkeypatch.setattr(hardening, "_store", _broken)
    monkeypatch.setattr(hardening, "_RETRY_SLEEP_S", 0.0)
    assert hardening.failures("acct:lock@example.com", 60) == hardening.LOCKED_OUT
    assert hardening.hit("anything", 100, 60) is False
    with pytest.raises(auth.LockedOut):  # the lockout survives the outage instead of evaporating
        auth.authenticate(session, user.email, "correct-horse")


def test_counter_store_falls_back_only_before_migrations(monkeypatch):
    """The one error we may answer from process memory is "the table does not exist yet"."""
    from sqlalchemy.exc import OperationalError

    def _no_table():
        raise OperationalError("SELECT 1", {}, Exception("no such table: rate_hits"))

    monkeypatch.setattr(hardening, "_store", _no_table)
    assert hardening.hit("boot", 2, 60) and hardening.hit("boot", 2, 60)
    assert hardening.hit("boot", 2, 60) is False and hardening.failures("boot", 60) == 2


def test_counters_share_the_engine_that_serves_requests(session):
    """Production topology: the counter store opens its own connection on the SAME engine the request session is
    already holding a transaction on. On SQLite that is the write-contention case, so exercise it for real."""
    engine = session.get_bind()
    hardening.use_engine(engine)
    hardening.reset_rate_limits()
    user = auth.register(session, "same@example.com", "correct-horse", "S")  # request transaction is open and dirty
    assert hardening.hit("shared", 2, 60) and hardening.hit("shared", 2, 60)
    assert hardening.hit("shared", 2, 60) is False
    assert hardening.failures("shared", 60) == 2
    hardening.reset_key("shared")
    assert hardening.failures("shared", 60) == 0
    session.flush()
    assert session.get(User, user.id) is not None  # the request transaction was never disturbed


def test_auth_mail_is_sent_out_of_band(session, monkeypatch):
    """/auth/forgot must not block on SMTP: it would pin the threadpool and time-leak which addresses exist."""
    from instilens.config import settings

    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(settings, "smtp_host", "smtp.example")
    monkeypatch.setattr(settings, "smtp_from", "no-reply@example")
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body: sent.append((to, subject, body)) or True)
    user = auth.register(session, "oob@example.com", "correct-horse", "O")
    session.flush()
    assert auth.request_password_reset(session, user.email) is True
    assert notify.flush_mail() and len(sent) == 1 and "/reset?token=" in sent[0][2]


def test_verification_without_smtp_mints_no_token_and_can_be_resent(client, session, monkeypatch):
    from instilens.config import settings

    monkeypatch.setattr(settings, "smtp_host", None)
    tok = _register(client)
    assert session.query(AuthToken).count() == 0  # no unusable single-use row per registration
    h = {"authorization": f"Bearer {tok}"}
    assert client.post("/api/v1/auth/verify/resend", headers=h).json()["sent"] is False
    monkeypatch.setattr(settings, "smtp_host", "smtp.example")
    monkeypatch.setattr(settings, "smtp_from", "no-reply@example")
    monkeypatch.setattr(notify, "send_email", lambda to, subject, body: True)
    assert client.post("/api/v1/auth/verify/resend", headers=h).json()["sent"] is True
    assert session.query(AuthToken).filter_by(kind="VERIFY").count() == 1
    assert notify.flush_mail()


def test_password_reset_also_verifies_the_address(client, session, outbox):
    _register(client)
    outbox.clear()
    client.post("/api/v1/auth/forgot", json={"email": "emre@example.com"})
    r = client.post("/api/v1/auth/reset", json={"token": _token_from(outbox, "/reset"), "new_password": "battery staple horse"})
    assert r.status_code == 200 and r.json()["user"]["email_verified"] is True
