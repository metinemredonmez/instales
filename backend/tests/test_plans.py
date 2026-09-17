"""Plans (services/plans): the feature matrix, the effective plan (own plan, manual expiry, organisation), gating
off by default, the 402 shape once it is on, and the caps on watchlist items, alert rules, portfolios and AI research."""

from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from instilens.config import settings
from instilens.domain.enums import OrgRole
from instilens.domain.models import Organization, OrgMember, User
from instilens.services import auth, plans


@pytest.fixture(autouse=True)
def _plans_off(monkeypatch):
    monkeypatch.setattr(settings, "plans_enforced", False)


def _client(session) -> TestClient:
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    return TestClient(app)


def _user(session, email: str, plan: str = "FREE", **fields) -> tuple[User, dict]:
    u = auth.register(session, email, "correct-horse-1", email.split("@")[0])
    u.plan = plan
    for k, v in fields.items():
        setattr(u, k, v)
    session.flush()
    return u, {"authorization": f"Bearer {auth.issue_token(u)}"}


def _org(session, owner: User, *members: User) -> Organization:
    org = Organization(name="Team", owner_user_id=owner.id)
    session.add(org)
    session.flush()
    now = datetime.now(UTC)
    session.add(OrgMember(org_id=org.id, user_id=owner.id, role=OrgRole.OWNER, invited_email=owner.email, accepted_at=now))
    for m in members:
        session.add(OrgMember(org_id=org.id, user_id=m.id, role=OrgRole.MEMBER, invited_email=m.email, accepted_at=now))
    session.flush()
    return org


# --- matrix -----------------------------------------------------------------------------------------


def test_matrix_and_upgrade_paths():
    m = plans.matrix()
    assert list(m) == ["FREE", "PRO", "PRO_PLUS"] and set(m["FREE"]) == set(plans.FEATURES)
    assert (m["FREE"]["watchlist_items"], m["FREE"]["alert_rules"], m["FREE"]["ai_research_per_day"]) == (10, 3, 5)
    assert m["FREE"]["portfolio"] is False and m["FREE"]["portfolios"] == 0 and m["FREE"]["tts"] is False and m["FREE"]["push"] is False and m["FREE"]["briefs"] is True
    assert (m["PRO"]["watchlist_items"], m["PRO"]["alert_rules"], m["PRO"]["ai_research_per_day"]) == (100, 50, 50)
    assert m["PRO"]["portfolio"] is True and (m["PRO"]["portfolios"], m["PRO"]["portfolio_positions"]) == (1, 100) and m["PRO"]["tts"] and m["PRO"]["push"]
    assert (m["PRO_PLUS"]["watchlist_items"], m["PRO_PLUS"]["alert_rules"], m["PRO_PLUS"]["ai_research_per_day"]) == (500, 500, 300)
    assert (m["PRO_PLUS"]["portfolios"], m["PRO_PLUS"]["org_seats"]) == (5, 10)
    assert m["PRO"]["org_seats"] == 0 and m["FREE"]["org_seats"] == 0
    # The matrix is what the plan page sells: nothing the product does not enforce or deliver is listed.
    assert "watchlists" not in plans.FEATURES and "priority_feed" not in plans.FEATURES
    assert plans.upgrade_for("portfolio", "FREE") == "PRO" and plans.upgrade_for("org_seats", "FREE") == "PRO_PLUS"
    assert plans.upgrade_for("watchlist_items", "PRO") == "PRO_PLUS" and plans.upgrade_for("alert_rules", "PRO_PLUS") == "PRO_PLUS"
    assert plans.features_of("BOGUS") == plans.features_of("FREE")


# --- resolution --------------------------------------------------------------------------------------


def test_effective_plan_own_manual_expiry_and_org(session, monkeypatch):
    u, _ = _user(session, "solo@example.com")
    assert plans.effective_plan(session, u) == ("FREE", "own")
    u.plan, u.plan_source, u.plan_until = "PRO", "manual", date.today() + timedelta(days=30)
    assert plans.effective_plan(session, u) == ("PRO", "manual") and plans.own_plan(u) == "PRO"
    u.plan_until = date.today() - timedelta(days=1)  # the grant ran out: reads as FREE, the row is untouched
    assert plans.effective_plan(session, u) == ("FREE", "own") and u.plan == "PRO"
    assert plans.own_plan(u, today=date.today() - timedelta(days=2)) == "PRO"

    owner, _ = _user(session, "owner@example.com", "PRO_PLUS", plan_source="stripe")
    member, _ = _user(session, "member@example.com")
    pending, _ = _user(session, "pending@example.com")
    org = _org(session, owner, member)
    session.add(OrgMember(org_id=org.id, user_id=None, role=OrgRole.MEMBER, invited_email=pending.email))  # invited, not accepted
    session.flush()
    assert plans.effective_plan(session, member) == ("PRO_PLUS", "org")  # inherits the owner's plan
    assert plans.effective_plan(session, owner) == ("PRO_PLUS", "own")
    assert plans.effective_plan(session, pending) == ("FREE", "own")  # an open invitation grants nothing
    assert (org.plan, org.seats) == ("PRO_PLUS", 10)  # mirror columns refreshed on resolution
    member.plan, member.plan_source = "PRO", "stripe"
    assert plans.effective_plan(session, member) == ("PRO_PLUS", "org")  # the org is still higher
    owner.plan = "PRO"
    assert plans.effective_plan(session, member) == ("PRO", "own")  # own plan ties the org's: own wins the label
    owner.plan = "FREE"
    assert plans.effective_plan(session, member) == ("PRO", "own") and plans.org_plan(session, org) == "FREE" and org.seats == 0
    assert plans.features(session, member)["portfolio"] is True

    # Gating is a runtime switch and never touches ADMIN.
    monkeypatch.setattr(settings, "plans_enforced", True)
    free, _ = _user(session, "free@example.com")
    assert plans.limit(session, free, "alert_rules") == 3 and plans.allows(session, free, "portfolio") is False
    with pytest.raises(plans.PlanLimit) as exc:
        plans.require(session, free, "portfolio")
    assert exc.value.detail() == {"detail": "plan_limit", "feature": "portfolio", "plan": "FREE", "limit": 0, "upgrade": "PRO"}
    with pytest.raises(plans.PlanLimit):
        plans.enforce_limit(session, str(free.id), "alert_rules", 3)
    plans.enforce_limit(session, str(free.id), "alert_rules", 2)  # one below the cap: allowed
    free.role = "ADMIN"
    assert plans.limit(session, free, "alert_rules") is None and plans.allows(session, free, "portfolio") is True
    plans.require(session, free, "portfolio")
    monkeypatch.setattr(settings, "plans_enforced", False)
    free.role = "USER"
    assert plans.limit(session, free, "alert_rules") is None
    plans.enforce_limit(session, "not-a-user", "alert_rules", 99)  # an unknown owner is never gated


def test_push_delivery_follows_the_plan(session, monkeypatch):
    """A device registered earlier (or straight with OneSignal) gets nothing on a plan without `push` while plans
    are enforced; the switch off, or a plan with it, delivers as before."""
    from instilens.services import notify

    calls: list[str] = []
    monkeypatch.setattr(notify, "send_push", lambda *a, **k: calls.append("push") or 1)
    monkeypatch.setattr(notify, "send_onesignal", lambda *a, **k: calls.append("onesignal") or True)
    monkeypatch.setattr(settings, "onesignal_app_id", "app")
    monkeypatch.setattr(settings, "onesignal_rest_api_key", "key")
    monkeypatch.setattr(settings, "plans_enforced", True)
    free, _ = _user(session, "quiet@example.com")
    assert notify.push_user(session, str(free.id), "t", "b", "l") is None and calls == []
    monkeypatch.setattr(settings, "onesignal_app_id", None)
    assert notify.push_user(session, str(free.id), "t", "b", "l") is None and calls == []
    free.plan, free.plan_source = "PRO", "stripe"
    session.flush()
    assert notify.push_user(session, str(free.id), "t", "b", "l") == "push" and calls == ["push"]
    free.plan = "FREE"
    monkeypatch.setattr(settings, "plans_enforced", False)
    assert notify.push_user(session, str(free.id), "t", "b", "l") == "push" and calls == ["push", "push"]


# --- the API: off by default, 402 shape when on --------------------------------------------------------------


def test_gating_off_by_default_and_402_shape_when_on(session, pipeline_run, monkeypatch):
    c = _client(session)
    u, h = _user(session, "gate@example.com")
    me = c.get("/api/v1/auth/me", headers=h).json()
    assert me["plan"] == "FREE" and me["own_plan"] == "FREE" and me["plan_source"] == "own" and me["plans_enforced"] is False
    assert me["features"] == plans.features_of("FREE")  # limits are reported either way
    r = c.post("/api/v1/portfolios", json={"name": "Deneme", "market": "TR"}, headers=h)
    assert r.status_code == 201  # not enforced: a FREE account keeps working
    assert c.get("/api/v1/portfolios", headers=h).status_code == 200

    monkeypatch.setattr(settings, "plans_enforced", True)
    r = c.get("/api/v1/portfolios", headers=h)
    assert r.status_code == 402 and r.json() == {"detail": "plan_limit", "feature": "portfolio", "plan": "FREE", "limit": 0, "upgrade": "PRO"}
    assert c.post("/api/v1/portfolios", json={"name": "X", "market": "TR"}, headers=h).status_code == 402
    assert c.post("/api/v1/push/subscribe", json={"endpoint": "https://push.example/1", "keys": {"p256dh": "k", "auth": "a"}}, headers=h).json()["feature"] == "push"
    ticket = c.post("/api/v1/auth/ticket", headers=h).json()["ticket"]
    assert c.get(f"/api/v1/ai-notes/1/audio?ticket={ticket}", headers={}).status_code == 402  # tts is a plan feature too
    assert c.get("/api/v1/auth/me", headers=h).json()["plans_enforced"] is True
    u.plan, u.plan_source = "PRO", "stripe"
    session.flush()
    assert c.get("/api/v1/portfolios", headers=h).status_code == 200
    assert c.get("/api/v1/auth/me", headers=h).json()["plan"] == "PRO"
    # ADMIN is never gated.
    a, ha = _user(session, "admin@example.com", role="ADMIN")
    assert c.get("/api/v1/portfolios", headers=ha).status_code == 200
    from instilens.api.main import app

    app.dependency_overrides.clear()


def test_limits_for_watchlists_rules_portfolios_and_ai(session, pipeline_run, monkeypatch):
    from instilens.api.routes import v1

    monkeypatch.setattr(settings, "plans_enforced", True)
    c = _client(session)
    u, h = _user(session, "caps@example.com")
    symbols = ["ASELS", "THYAO", "SASA", "KCHOL", "EREGL", "ANELE"]
    funds = ["TMV", "MAC", "TLY", "TI2", "IPB"]
    codes = [c.post("/api/v1/watchlist", json={"symbol": s}, headers=h).status_code for s in symbols] + [c.post("/api/v1/watchlist", json={"fund_code": f}, headers=h).status_code for f in funds]
    assert codes[:10] == [201] * 10 and codes[10] == 402  # FREE: 10 items per watchlist
    r = c.post("/api/v1/watchlist", json={"fund_code": "IPB"}, headers=h)
    assert r.json() == {"detail": "plan_limit", "feature": "watchlist_items", "plan": "FREE", "limit": 10, "upgrade": "PRO"}
    assert len(c.get("/api/v1/watchlist", headers=h).json()) == 10  # nothing above the cap was written
    assert c.post("/api/v1/watchlist", json={"symbol": "ASELS"}, headers=h).status_code == 201  # a duplicate is not an addition

    rules = [c.post("/api/v1/alerts/rules", json={"symbol": s, "rule_type": "NEW_FUND_POSITION"}, headers=h) for s in symbols[:4]]
    assert [r.status_code for r in rules] == [201, 201, 201, 402] and rules[3].json()["feature"] == "alert_rules" and rules[3].json()["limit"] == 3
    assert c.delete(f"/api/v1/alerts/rules/{rules[0].json()['id']}", headers=h).status_code == 204  # a soft-deleted rule frees its slot
    assert c.post("/api/v1/alerts/rules", json={"symbol": "KCHOL", "rule_type": "NEW_FUND_POSITION"}, headers=h).status_code == 201

    u.plan, u.plan_source = "PRO", "stripe"
    session.flush()
    assert c.post("/api/v1/portfolios", json={"name": "Bir", "market": "TR"}, headers=h).status_code == 201
    r = c.post("/api/v1/portfolios", json={"name": "İki", "market": "TR"}, headers=h)
    assert r.status_code == 402 and r.json() == {"detail": "plan_limit", "feature": "portfolios", "plan": "PRO", "limit": 1, "upgrade": "PRO_PLUS"}
    u.plan = "PRO_PLUS"
    session.flush()
    assert c.post("/api/v1/portfolios", json={"name": "İki", "market": "TR"}, headers=h).status_code == 201

    # AI research: the plan's daily quota rides on the same counter store as the hourly budget.
    class _Engine:
        def ask(self, question, market):
            from instilens.ai.engine import ResearchAnswer

            return ResearchAnswer(question=question, answer="x", model="test")

    monkeypatch.setattr(v1, "build_engine", lambda s: _Engine())
    u.plan = "FREE"
    session.flush()
    codes = [c.post("/api/v1/research", json={"question": "ASELS kim alıyor?", "market": "TR"}, headers=h).status_code for _ in range(6)]
    assert codes == [200] * 5 + [402]
    r = c.post("/api/v1/research", json={"question": "ASELS kim alıyor?", "market": "TR"}, headers=h)
    assert r.json() == {"detail": "plan_limit", "feature": "ai_research_per_day", "plan": "FREE", "limit": 5, "upgrade": "PRO"}
    u.plan = "PRO"
    session.flush()
    assert c.post("/api/v1/research", json={"question": "ASELS kim alıyor?", "market": "TR"}, headers=h).status_code == 200  # a higher plan, a higher quota
    monkeypatch.setattr(settings, "plans_enforced", False)
    u.plan = "FREE"
    session.flush()
    assert c.post("/api/v1/research", json={"question": "ASELS kim alıyor?", "market": "TR"}, headers=h).status_code == 200  # off: only the hourly budget
    from instilens.api.main import app

    app.dependency_overrides.clear()
