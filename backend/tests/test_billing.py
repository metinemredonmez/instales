"""Billing (services/billing, /api/v1/billing): 503 while Stripe is not configured, a fake provider behind the
checkout / portal routes, the signed webhook (a bad signature is 400; checkout.session.completed → ACTIVE + plan,
customer.subscription.updated / deleted, invoice.payment_failed, an idempotent replay), the money-relevant
negatives (a second checkout on a live subscription is 409, an unpaid checkout or an incomplete / paused
subscription grants nothing, the price wins over stale metadata, an out-of-order event is skipped), the Stripe
provider's documented call shapes against a stub client, and manual grants from the admin Users page, which stop
at a paid subscription. Network-free."""

import hashlib
import hmac
import json
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from instilens.config import settings
from instilens.domain.models import AuditEvent, ProcessedWebhook, Subscription, User
from instilens.services import auth, billing

SECRET = "whsec_test_secret"


@pytest.fixture(autouse=True)
def _stripe_provider(monkeypatch):
    """Every test starts on the real (unconfigured) Stripe provider with gating off."""
    monkeypatch.setattr(settings, "plans_enforced", False)
    for k in ("stripe_secret_key", "stripe_webhook_secret", "stripe_price_pro", "stripe_price_pro_plus"):
        monkeypatch.setattr(settings, k, None)
    billing.use_provider(None)
    yield
    billing.use_provider(None)


def _configure(monkeypatch):
    monkeypatch.setattr(settings, "stripe_secret_key", "sk_test_x")
    monkeypatch.setattr(settings, "stripe_webhook_secret", SECRET)
    monkeypatch.setattr(settings, "stripe_price_pro", "price_pro")
    monkeypatch.setattr(settings, "stripe_price_pro_plus", "price_pro_plus")


def _client(session, email: str = "b@example.com", role: str = "USER") -> tuple[TestClient, dict, User]:
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    u = auth.register(session, email, "password123", "B")
    u.role = role
    session.flush()
    return TestClient(app), {"authorization": f"Bearer {auth.issue_token(u)}"}, u


def _signed(payload: dict, secret: str = SECRET, ts: int | None = None) -> tuple[bytes, str]:
    """A body and its Stripe-Signature header (`t=…,v1=HMAC-SHA256(secret, "t.body")`), as the SDK verifies it."""
    body = json.dumps(payload).encode()
    ts = ts or int(time.time())
    return body, f"t={ts},v1={hmac.new(secret.encode(), f'{ts}.'.encode() + body, hashlib.sha256).hexdigest()}"


def _event(event_id: str, kind: str, obj: dict, created: int | None = None) -> dict:
    out = {"id": event_id, "object": "event", "type": kind, "data": {"object": obj}}
    if created is not None:
        out["created"] = created
    return out


class _Fake:
    """A PaymentProvider that never leaves the process: fixed URLs, a trivial signature ("ok")."""

    name = "fake"

    def __init__(self, configured: bool = True) -> None:
        self._configured = configured
        self.calls: list[tuple] = []

    def configured(self) -> bool:
        return self._configured

    def price(self, plan):
        return {"id": f"price_{plan.lower()}", "amount": 9.0 if plan == "PRO" else 29.0, "currency": "usd", "interval": "month"} if self._configured else None

    def checkout_session(self, session, user, plan, success_url, cancel_url):
        if not self._configured:
            raise billing.NotConfigured("payments not configured")
        self.calls.append(("checkout", user.id, plan, success_url, cancel_url))
        return f"https://checkout.example/{plan}"

    def portal_session(self, session, user, return_url, flow=None):
        if not self._configured:
            raise billing.NotConfigured("payments not configured")
        if billing.customer_id(session, user) is None:
            raise billing.NoBillingAccount("no subscription to manage")
        self.calls.append(("portal", user.id, return_url, flow))
        return "https://portal.example/session"

    def handle_webhook(self, session, payload, sig):
        if sig != "ok":
            raise billing.WebhookError("invalid signature")
        raw = json.loads(payload)
        return billing.apply_event(session, billing.WebhookEvent(id=raw["id"], type=raw["type"], object=raw["data"]["object"]), provider=self.name)


# --- unconfigured -----------------------------------------------------------------------------------


def test_checkout_portal_and_webhook_answer_503_when_unconfigured(session):
    c, h, _ = _client(session)
    assert not billing.configured()
    r = c.post("/api/v1/billing/checkout", json={"plan": "PRO"}, headers=h)
    assert r.status_code == 503 and r.json()["detail"] == "payments not configured"
    assert c.post("/api/v1/billing/portal", headers=h).status_code == 503
    assert c.post("/api/v1/billing/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=x"}).status_code == 503
    assert c.post("/api/v1/billing/checkout", json={"plan": "FREE"}, headers=h).status_code == 422
    plans = c.get("/api/v1/billing/plans", headers=h).json()
    assert plans["configured"] is False and plans["provider"] == "stripe" and plans["currency"] == "usd" and plans["plans_enforced"] is False
    assert [p["code"] for p in plans["plans"]] == ["FREE", "PRO", "PRO_PLUS"] and all(p["price"] is None for p in plans["plans"])  # no price is ever invented
    assert plans["plans"][1]["features"]["portfolio"] is True and "watchlist_items" in plans["features"]
    me = c.get("/api/v1/billing/me", headers=h).json()
    assert (me["plan"], me["source"], me["subscription"], me["org"], me["configured"]) == ("FREE", "own", None, None, False)
    assert c.get("/api/v1/billing/plans").status_code == 401  # data routes need a user; the webhook is the only open one
    from instilens.api.main import app

    app.dependency_overrides.clear()


# --- fake provider ----------------------------------------------------------------------------------


def test_fake_provider_checkout_portal_and_me(session, monkeypatch):
    fake = _Fake()
    billing.use_provider(fake)
    monkeypatch.setattr(settings, "public_url", "https://app.example/")
    c, h, u = _client(session)
    plans = c.get("/api/v1/billing/plans", headers=h).json()
    assert plans["configured"] is True and plans["provider"] == "fake" and plans["plans"][1]["price"] == {"id": "price_pro", "amount": 9.0, "currency": "usd", "interval": "month"}
    assert plans["plans"][0]["price"] is None
    r = c.post("/api/v1/billing/checkout", json={"plan": "PRO_PLUS"}, headers=h)
    assert r.status_code == 200 and r.json() == {"url": "https://checkout.example/PRO_PLUS", "plan": "PRO_PLUS"}
    assert fake.calls == [("checkout", u.id, "PRO_PLUS", "https://app.example/plan?checkout=success&session_id={CHECKOUT_SESSION_ID}", "https://app.example/plan?checkout=cancel")]
    assert c.post("/api/v1/billing/portal", headers=h).status_code == 404  # never checked out: nothing to manage
    # The webhook lands the subscription; the portal then knows the customer.
    body = json.dumps(_event("evt_1", "checkout.session.completed", {"id": "cs_1", "mode": "subscription", "customer": "cus_1", "subscription": "sub_1", "payment_status": "paid", "metadata": {"user_id": str(u.id), "plan": "PRO_PLUS"}})).encode()
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": "nope"}).status_code == 400
    r = c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": "ok"})
    assert r.status_code == 200 and r.json()["applied"] is True and r.json()["status"] == "ACTIVE"
    r = c.post("/api/v1/billing/portal", headers=h)
    assert r.status_code == 200 and r.json() == {"url": "https://portal.example/session"} and fake.calls[-1] == ("portal", u.id, "https://app.example/plan", None)
    me = c.get("/api/v1/billing/me", headers=h).json()
    assert (me["plan"], me["source"], me["own_plan"], me["plan_until"]) == ("PRO_PLUS", "own", "PRO_PLUS", None)
    assert me["subscription"]["plan"] == "PRO_PLUS" and me["subscription"]["status"] == "ACTIVE" and me["subscription"]["provider"] == "fake"
    assert c.get("/api/v1/auth/me", headers=h).json()["plan"] == "PRO_PLUS"
    # A live subscription is changed through the portal, never doubled by a second checkout: 409, provider untouched.
    n = len(fake.calls)
    r = c.post("/api/v1/billing/checkout", json={"plan": "PRO"}, headers=h)
    assert r.status_code == 409 and r.json()["detail"] == "subscription_exists" and len(fake.calls) == n
    assert c.post("/api/v1/billing/portal", json={"flow": "subscription_update"}, headers=h).status_code == 200 and fake.calls[-1][3] == "subscription_update"
    assert c.post("/api/v1/billing/portal", json={"flow": "bogus"}, headers=h).status_code == 422
    kinds = [e.kind for e in session.scalars(select(AuditEvent))]
    assert "billing.checkout" in kinds and "billing.portal" in kinds
    fake._configured = False
    assert c.post("/api/v1/billing/checkout", json={"plan": "PRO"}, headers=h).status_code == 503
    from instilens.api.main import app

    app.dependency_overrides.clear()


# --- the Stripe webhook -----------------------------------------------------------------------------


def test_stripe_webhook_signature_and_lifecycle(session, monkeypatch):
    _configure(monkeypatch)
    assert billing.configured()
    c, h, u = _client(session)
    period_end = int(datetime(2026, 10, 17, 12, 0, tzinfo=UTC).timestamp())
    completed = _event("evt_100", "checkout.session.completed", {
        "id": "cs_test_1", "object": "checkout.session", "mode": "subscription", "customer": "cus_9", "subscription": "sub_9",
        "payment_status": "paid", "client_reference_id": str(u.id), "metadata": {"user_id": str(u.id), "plan": "PRO"},
    })
    body, sig = _signed(completed)
    # Signature failures: wrong secret, tampered body, missing header, stale timestamp.
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": _signed(completed, secret="whsec_other")[1]}).status_code == 400
    assert c.post("/api/v1/billing/webhook", content=body + b" ", headers={"stripe-signature": sig}).status_code == 400
    assert c.post("/api/v1/billing/webhook", content=body).status_code == 400
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": _signed(completed, ts=int(time.time()) - 3600)[1]}).status_code == 400
    assert session.scalar(select(ProcessedWebhook)) is None and session.get(User, u.id).plan == "FREE"
    bad_json, bad_sig = _signed({"id": "x"})
    assert c.post("/api/v1/billing/webhook", content=b"not json", headers={"stripe-signature": f"t={bad_sig.split(',')[0][2:]},v1={hmac.new(SECRET.encode(), f'{bad_sig.split(',')[0][2:]}.not json'.encode(), hashlib.sha256).hexdigest()}"}).status_code == 400

    # checkout.session.completed → ACTIVE, the account on PRO.
    r = c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig})
    assert r.status_code == 200 and r.json() == {"received": True, "applied": True, "type": "checkout.session.completed", "user_id": u.id, "plan": "PRO", "status": "ACTIVE"}
    sub = session.scalar(select(Subscription).where(Subscription.provider_subscription_id == "sub_9"))
    assert (sub.user_id, sub.plan, sub.status, sub.provider, sub.provider_customer_id) == (u.id, "PRO", "ACTIVE", "stripe", "cus_9")
    user = session.get(User, u.id)
    assert (user.plan, user.plan_source, user.plan_until) == ("PRO", "stripe", None)
    # Idempotent replay: acknowledged, nothing applied twice.
    r = c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": _signed(completed)[1]})
    assert r.status_code == 200 and r.json()["duplicate"] is True and r.json()["applied"] is False
    assert len(session.scalars(select(Subscription)).all()) == 1 and len(session.scalars(select(ProcessedWebhook)).all()) == 1

    # customer.subscription.updated: the period, cancel-at-period-end, a plan change read off the price id — the
    # checkout-time metadata still says PRO (it is never rewritten), the price says what is billed.
    updated = _event("evt_101", "customer.subscription.updated", {
        "id": "sub_9", "object": "subscription", "customer": "cus_9", "status": "active", "cancel_at_period_end": True,
        "items": {"data": [{"price": {"id": "price_pro_plus"}, "current_period_end": period_end}]}, "metadata": {"user_id": str(u.id), "plan": "PRO"},
    }, created=1_000_100)
    body, sig = _signed(updated)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["plan"] == "PRO_PLUS"
    session.refresh(sub)
    assert (sub.plan, sub.status, sub.cancel_at_period_end, sub.current_period_end.replace(tzinfo=None)) == ("PRO_PLUS", "ACTIVE", True, datetime(2026, 10, 17, 12, 0))
    assert session.get(User, u.id).plan == "PRO_PLUS"
    me = c.get("/api/v1/billing/me", headers=h).json()
    assert me["subscription"]["cancel_at_period_end"] is True and me["subscription"]["current_period_end"].startswith("2026-10-17")
    assert "note" not in me["subscription"]
    # The other direction: a portal downgrade to the PRO price with the metadata still saying PRO_PLUS lands as PRO.
    down = _event("evt_101b", "customer.subscription.updated", {"id": "sub_9", "object": "subscription", "customer": "cus_9", "status": "active",
                                                                 "items": {"data": [{"price": {"id": "price_pro"}}]}, "metadata": {"plan": "PRO_PLUS"}}, created=1_000_101)
    body, sig = _signed(down)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["plan"] == "PRO"
    assert session.get(User, u.id).plan == "PRO"
    # An event older than the last one applied is skipped (Stripe does not deliver in order): the PRO_PLUS update again.
    body, sig = _signed({**updated, "id": "evt_101c"})
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["reason"] == "stale event"
    assert session.get(User, u.id).plan == "PRO"
    # A stale event still fills what the row lacks (the period end of a subscription.created that trails its checkout), never status or plan.
    sub.current_period_end = None
    session.flush()
    body, sig = _signed({**updated, "id": "evt_101cc"})
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["reason"] == "stale event"
    session.refresh(sub)
    assert sub.current_period_end.replace(tzinfo=None) == datetime(2026, 10, 17, 12, 0) and sub.plan == "PRO"
    # Paused grants nothing: the account drops to FREE until the subscription is active again.
    paused = _event("evt_101d", "customer.subscription.updated", {"id": "sub_9", "object": "subscription", "customer": "cus_9", "status": "paused",
                                                                   "items": {"data": [{"price": {"id": "price_pro"}}]}}, created=1_000_102)
    body, sig = _signed(paused)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["status"] == "INCOMPLETE"
    assert (session.get(User, u.id).plan, session.get(User, u.id).plan_source) == ("FREE", None)
    body, sig = _signed(_event("evt_101e", "customer.subscription.updated", {"id": "sub_9", "object": "subscription", "customer": "cus_9", "status": "active",
                                                                             "items": {"data": [{"price": {"id": "price_pro_plus"}}]}}, created=1_000_103))
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["plan"] == "PRO_PLUS"
    assert session.get(User, u.id).plan == "PRO_PLUS"

    # invoice.payment_failed: PAST_DUE, the plan kept while Stripe retries.
    failed = _event("evt_102", "invoice.payment_failed", {"id": "in_1", "object": "invoice", "customer": "cus_9", "parent": {"subscription_details": {"subscription": "sub_9"}}})
    body, sig = _signed(failed)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["status"] == "PAST_DUE"
    assert session.get(User, u.id).plan == "PRO_PLUS"
    # An event type nobody handles is acknowledged and ignored.
    body, sig = _signed(_event("evt_103", "customer.created", {"id": "cus_9"}))
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json() == {"received": True, "applied": False, "ignored": True, "type": "customer.created"}

    # customer.subscription.deleted → CANCELED, the account back on FREE.
    deleted = _event("evt_104", "customer.subscription.deleted", {"id": "sub_9", "object": "subscription", "customer": "cus_9", "status": "canceled", "current_period_end": period_end}, created=1_000_200)
    body, sig = _signed(deleted)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["status"] == "CANCELED"
    user = session.get(User, u.id)
    assert (user.plan, user.plan_source) == ("FREE", None)
    me = c.get("/api/v1/billing/me", headers=h).json()
    assert me["plan"] == "FREE" and me["subscription"]["status"] == "CANCELED"  # the row stays for the record
    # A stale `updated` (active, dated before the deletion) delivered late does not resurrect it.
    late = _event("evt_104b", "customer.subscription.updated", {"id": "sub_9", "object": "subscription", "customer": "cus_9", "status": "active",
                                                                 "items": {"data": [{"price": {"id": "price_pro_plus"}}]}}, created=1_000_150)
    body, sig = _signed(late)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["reason"] == "stale event"
    session.refresh(sub)
    assert sub.status == "CANCELED" and session.get(User, u.id).plan == "FREE"
    # A deleted event for an unknown subscription changes nothing; a checkout for an unknown user is ignored.
    body, sig = _signed(_event("evt_105", "customer.subscription.deleted", {"id": "sub_unknown", "status": "canceled"}))
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["ignored"] is True
    body, sig = _signed(_event("evt_106", "checkout.session.completed", {"id": "cs_2", "mode": "subscription", "metadata": {"user_id": "999999", "plan": "PRO"}}))
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["ignored"] is True
    assert session.get(User, u.id).plan == "FREE"
    from instilens.api.main import app

    app.dependency_overrides.clear()


def test_nothing_is_granted_before_payment(session, monkeypatch):
    """An unpaid checkout (delayed payment method), an `incomplete` subscription and a price that is not one of ours
    leave the account on FREE; the plan arrives with the subscription event that says active."""
    _configure(monkeypatch)
    c, h, u = _client(session)
    unpaid = _event("evt_200", "checkout.session.completed", {"id": "cs_u", "mode": "subscription", "customer": "cus_u", "subscription": "sub_u",
                                                               "payment_status": "unpaid", "metadata": {"user_id": str(u.id), "plan": "PRO"}}, created=2_000_000)
    body, sig = _signed(unpaid)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["status"] == "INCOMPLETE"
    assert session.get(User, u.id).plan == "FREE" and c.get("/api/v1/billing/me", headers=h).json()["subscription"]["status"] == "INCOMPLETE"
    body, sig = _signed(_event("evt_201", "customer.subscription.updated", {"id": "sub_u", "object": "subscription", "customer": "cus_u", "status": "incomplete",
                                                                            "items": {"data": [{"price": {"id": "price_pro"}}]}}, created=2_000_001))
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["status"] == "INCOMPLETE"
    assert session.get(User, u.id).plan == "FREE"
    body, sig = _signed(_event("evt_202", "customer.subscription.updated", {"id": "sub_u", "object": "subscription", "customer": "cus_u", "status": "active",
                                                                            "items": {"data": [{"price": {"id": "price_pro"}}]}}, created=2_000_002))
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["status"] == "ACTIVE"
    assert (session.get(User, u.id).plan, session.get(User, u.id).plan_source) == ("PRO", "stripe")
    # customer.subscription.created before any checkout: `incomplete` lands as an INCOMPLETE row; an unknown price is ignored.
    other = auth.register(session, "o@example.com", "password123", "O")
    session.flush()
    body, sig = _signed(_event("evt_203", "customer.subscription.created", {"id": "sub_o", "object": "subscription", "customer": "cus_o", "status": "incomplete",
                                                                            "items": {"data": [{"price": {"id": "price_pro_plus"}}]}, "metadata": {"user_id": str(other.id)}}, created=2_000_003))
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["status"] == "INCOMPLETE"
    assert session.get(User, other.id).plan == "FREE"
    body, sig = _signed(_event("evt_204", "customer.subscription.created", {"id": "sub_x", "object": "subscription", "customer": "cus_o", "status": "active",
                                                                            "items": {"data": [{"price": {"id": "price_unknown"}}]}, "metadata": {"user_id": str(other.id)}}, created=2_000_004))
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["reason"] == "unknown plan"
    assert session.get(User, other.id).plan == "FREE" and session.scalar(select(Subscription).where(Subscription.provider_subscription_id == "sub_x")) is None
    from instilens.api.main import app

    app.dependency_overrides.clear()


def test_a_second_live_subscription_and_a_higher_manual_grant(session, monkeypatch):
    """Two live Stripe rows for one account (only possible outside our checkout): cancelling one keeps the other's
    plan. A manual grant above the paid plan is not undone by the paid subscription's events."""
    _configure(monkeypatch)
    c, h, u = _client(session)
    ev = lambda i, kind, obj, at: _signed(_event(f"evt_3{i}", kind, obj, created=at))  # noqa: E731
    body, sig = ev(0, "checkout.session.completed", {"id": "cs_a", "mode": "subscription", "customer": "cus_a", "subscription": "sub_a", "payment_status": "paid", "metadata": {"user_id": str(u.id), "plan": "PRO"}}, 3_000_000)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["plan"] == "PRO"
    body, sig = ev(1, "customer.subscription.created", {"id": "sub_b", "object": "subscription", "customer": "cus_a", "status": "active", "items": {"data": [{"price": {"id": "price_pro_plus"}}]}, "metadata": {"user_id": str(u.id)}}, 3_000_001)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["plan"] == "PRO_PLUS"
    rows = {r.provider_subscription_id: r for r in session.scalars(select(Subscription).where(Subscription.user_id == u.id))}
    assert (rows["sub_a"].status, rows["sub_b"].status, session.get(User, u.id).plan) == ("CANCELED", "ACTIVE", "PRO_PLUS")  # one live paid row
    body, sig = ev(2, "customer.subscription.deleted", {"id": "sub_a", "object": "subscription", "status": "canceled"}, 3_000_002)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["status"] == "CANCELED"
    assert (session.get(User, u.id).plan, session.get(User, u.id).plan_source) == ("PRO_PLUS", "stripe")  # the live one still counts
    body, sig = ev(3, "customer.subscription.deleted", {"id": "sub_b", "object": "subscription", "status": "canceled"}, 3_000_003)
    c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig})
    assert session.get(User, u.id).plan == "FREE"

    # An admin PRO_PLUS on an account that then pays for PRO: the grant stays, the paid row is recorded, no renewal event undoes it.
    billing.manual.grant(session, u, "PRO_PLUS", note="goodwill", actor="root@example.com")
    body, sig = ev(4, "checkout.session.completed", {"id": "cs_c", "mode": "subscription", "customer": "cus_a", "subscription": "sub_c", "payment_status": "paid", "metadata": {"user_id": str(u.id), "plan": "PRO"}}, 3_000_004)
    assert c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig}).json()["status"] == "ACTIVE"
    assert (session.get(User, u.id).plan, session.get(User, u.id).plan_source) == ("PRO_PLUS", "manual")
    live = {r.provider: r.status for r in session.scalars(select(Subscription).where(Subscription.user_id == u.id, Subscription.status == "ACTIVE"))}
    assert live == {"manual": "ACTIVE", "stripe": "ACTIVE"}
    body, sig = ev(5, "customer.subscription.updated", {"id": "sub_c", "object": "subscription", "customer": "cus_a", "status": "active", "items": {"data": [{"price": {"id": "price_pro"}}]}}, 3_000_005)
    c.post("/api/v1/billing/webhook", content=body, headers={"stripe-signature": sig})
    assert (session.get(User, u.id).plan, session.get(User, u.id).plan_source) == ("PRO_PLUS", "manual")
    # The admin can still date or lift their own grant — lifting it leaves the paid plan in force — but not grant
    # a paid plan over the live subscription, nor date an account whose plan is that subscription: Stripe is where it changes.
    ca, ha, _ = _client(session, "root@example.com", role="ADMIN")
    until = (date.today() + timedelta(days=9)).isoformat()
    assert ca.patch(f"/api/v1/admin/users/{u.id}", json={"plan_until": until}, headers=ha).json()["plan_until"] == until
    r = ca.patch(f"/api/v1/admin/users/{u.id}", json={"plan": "FREE"}, headers=ha)
    assert r.status_code == 200 and (r.json()["plan"], r.json()["plan_source"], r.json()["plan_until"]) == ("PRO", "stripe", None)
    assert all(r.status == "CANCELED" for r in session.scalars(select(Subscription).where(Subscription.user_id == u.id, Subscription.provider == "manual")))
    r = ca.patch(f"/api/v1/admin/users/{u.id}", json={"plan": "PRO_PLUS"}, headers=ha)
    assert r.status_code == 400 and r.json()["detail"].startswith("managed by stripe")
    r = ca.patch(f"/api/v1/admin/users/{u.id}", json={"plan_until": until}, headers=ha)
    assert r.status_code == 400 and r.json()["detail"].startswith("managed by stripe")
    assert (session.get(User, u.id).plan, session.get(User, u.id).plan_source) == ("PRO", "stripe")
    from instilens.api.main import app

    app.dependency_overrides.clear()


def test_stripe_provider_uses_the_documented_calls(session, monkeypatch):
    """The SDK client is stubbed at `_api()`: what reaches Stripe is a Checkout Session in subscription mode with
    the plan's price, a Billing Portal session for the stored customer, and a Price retrieve for the plan page."""
    _configure(monkeypatch)
    calls: list[tuple] = []

    class _Obj:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class _Sessions:
        def create(self, params=None, options=None):
            calls.append(("checkout.sessions.create", params))
            return _Obj(url="https://checkout.stripe.com/c/pay/cs_test")

    class _Portal:
        def create(self, params=None, options=None):
            calls.append(("billing_portal.sessions.create", params))
            return _Obj(url="https://billing.stripe.com/p/session/x")

    class _Prices:
        def retrieve(self, price, params=None, options=None):
            calls.append(("prices.retrieve", price))
            return _Obj(unit_amount=990 if price == "price_pro" else 2990, currency="usd", recurring=_Obj(interval="month"), tax_behavior="inclusive")

    class _Client:
        v1 = _Obj(checkout=_Obj(sessions=_Sessions()), billing_portal=_Obj(sessions=_Portal()), prices=_Prices())

    provider = billing.StripeProvider()
    monkeypatch.setattr(provider, "_api", lambda: _Client())
    billing.use_provider(provider)
    c, h, u = _client(session)
    r = c.post("/api/v1/billing/checkout", json={"plan": "PRO"}, headers=h)
    assert r.status_code == 200 and r.json()["url"].startswith("https://checkout.stripe.com/")
    name, params = calls[-1]
    assert name == "checkout.sessions.create" and params["mode"] == "subscription" and params["line_items"] == [{"price": "price_pro", "quantity": 1}]
    assert params["metadata"] == {"user_id": str(u.id), "plan": "PRO"} and params["subscription_data"] == {"metadata": {"user_id": str(u.id), "plan": "PRO"}}
    assert params["customer_email"] == u.email and "customer" not in params and params["client_reference_id"] == str(u.id)
    assert params["success_url"].endswith("/plan?checkout=success&session_id={CHECKOUT_SESSION_ID}") and params["cancel_url"].endswith("/plan?checkout=cancel")
    assert "automatic_tax" not in params and "consent_collection" not in params  # both need dashboard setup: opt-in
    assert c.post("/api/v1/billing/portal", headers=h).status_code == 404
    sub = Subscription(user_id=u.id, plan="PRO", status="ACTIVE", provider="stripe", provider_customer_id="cus_known", provider_subscription_id="sub_known")
    session.add(sub)
    session.flush()
    assert c.post("/api/v1/billing/portal", headers=h).json()["url"].startswith("https://billing.stripe.com/")
    assert calls[-1] == ("billing_portal.sessions.create", {"customer": "cus_known", "return_url": f"{settings.public_url.rstrip('/')}/plan"})
    # A plan change on a live subscription: the portal's subscription_update flow on that subscription, and 409 for a new checkout.
    assert c.post("/api/v1/billing/portal", json={"flow": "subscription_update"}, headers=h).status_code == 200
    assert calls[-1][1]["flow_data"] == {"type": "subscription_update", "subscription_update": {"subscription": "sub_known"}}
    n = len(calls)
    assert c.post("/api/v1/billing/checkout", json={"plan": "PRO_PLUS"}, headers=h).status_code == 409 and len(calls) == n
    sub.status = "CANCELED"
    session.flush()
    assert c.post("/api/v1/billing/portal", json={"flow": "subscription_update"}, headers=h).status_code == 404  # nothing live to change
    monkeypatch.setattr(settings, "stripe_automatic_tax", True)
    monkeypatch.setattr(settings, "stripe_terms_consent", True)
    c.post("/api/v1/billing/checkout", json={"plan": "PRO_PLUS"}, headers=h)
    assert calls[-1][1]["customer"] == "cus_known" and "customer_email" not in calls[-1][1]  # a known customer is reused
    assert calls[-1][1]["automatic_tax"] == {"enabled": True} and calls[-1][1]["consent_collection"] == {"terms_of_service": "required"}
    plans = c.get("/api/v1/billing/plans", headers=h).json()
    assert plans["configured"] is True and plans["plans"][1]["price"] == {"id": "price_pro", "amount": 9.9, "currency": "usd", "interval": "month", "tax_behavior": "inclusive"}
    assert plans["plans"][2]["price"]["amount"] == 29.9
    n = len(calls)
    c.get("/api/v1/billing/plans", headers=h)
    assert len(calls) == n  # prices are cached, not re-read on every visit
    from instilens.api.main import app

    app.dependency_overrides.clear()


# --- manual grants ----------------------------------------------------------------------------------


def test_manual_subscription_by_admin(session, monkeypatch):
    c, ha, admin = _client(session, "root@example.com", role="ADMIN")
    target = auth.register(session, "t@example.com", "password123", "T")
    session.flush()
    until = (date.today() + timedelta(days=30)).isoformat()
    r = c.patch(f"/api/v1/admin/users/{target.id}", json={"plan": "PRO", "plan_until": until, "plan_note": "beta tester"}, headers=ha)
    assert r.status_code == 200 and (r.json()["plan"], r.json()["plan_source"], r.json()["plan_until"], r.json()["effective_plan"]) == ("PRO", "manual", until, "PRO")
    sub = session.scalar(select(Subscription).where(Subscription.user_id == target.id))
    assert (sub.provider, sub.plan, sub.status, sub.note) == ("manual", "PRO", "ACTIVE", "root@example.com: beta tester") and sub.current_period_end.date().isoformat() == until
    listed = next(x for x in c.get("/api/v1/admin/users", headers=ha).json() if x["id"] == target.id)
    assert (listed["plan_source"], listed["plan_until"], listed["effective_source"]) == ("manual", until, "manual")
    ht = {"authorization": f"Bearer {auth.issue_token(target)}"}
    me = c.get("/api/v1/billing/me", headers=ht).json()
    assert (me["plan"], me["source"], me["plan_until"], me["subscription"]["provider"]) == ("PRO", "manual", until, "manual")
    assert "note" not in me["subscription"]  # who granted it and why is for the admin pages, not the customer
    assert c.patch(f"/api/v1/admin/users/{target.id}", json={"plan": "PRO", "plan_until": "2020-01-01"}, headers=ha).status_code == 400
    # The date alone (the admin page's "manual until" field) re-dates the grant in place: same row, new expiry; null lifts it.
    later = (date.today() + timedelta(days=90)).isoformat()
    assert c.patch(f"/api/v1/admin/users/{target.id}", json={"plan_until": later}, headers=ha).json()["plan_until"] == later
    session.refresh(sub)
    assert sub.status == "ACTIVE" and sub.current_period_end.date().isoformat() == later and session.scalar(select(func.count(Subscription.id)).where(Subscription.user_id == target.id)) == 1
    assert c.patch(f"/api/v1/admin/users/{target.id}", json={"plan_until": None}, headers=ha).json()["plan_until"] is None and sub.current_period_end is None
    # A second grant replaces the first; FREE revokes and clears the source.
    assert c.patch(f"/api/v1/admin/users/{target.id}", json={"plan": "PRO_PLUS"}, headers=ha).json()["plan_until"] is None
    rows = session.scalars(select(Subscription).where(Subscription.user_id == target.id).order_by(Subscription.id)).all()
    assert [(r.plan, r.status) for r in rows] == [("PRO", "CANCELED"), ("PRO_PLUS", "ACTIVE")]
    assert c.patch(f"/api/v1/admin/users/{target.id}", json={"plan": "FREE"}, headers=ha).json()["plan_source"] is None
    assert all(r.status == "CANCELED" for r in session.scalars(select(Subscription).where(Subscription.user_id == target.id)))
    assert c.patch(f"/api/v1/admin/users/{target.id}", json={"plan_until": later}, headers=ha).status_code == 400  # nothing manual to date on FREE
    assert c.get("/api/v1/billing/me", headers=ht).json()["plan"] == "FREE"
    # The grant reads as FREE past its date without anyone touching the row (services/plans.own_plan).
    billing.manual.grant(session, target, "PRO", until=date.today() - timedelta(days=1), note="expired", actor="root@example.com")
    assert target.plan == "PRO" and c.get("/api/v1/auth/me", headers=ht).json()["plan"] == "FREE"
    # A plan set before grants were recorded (no source) is adopted as manual on its first re-date.
    legacy = auth.register(session, "old@example.com", "password123", "Old")
    legacy.plan = "PRO_PLUS"
    session.flush()
    assert c.patch(f"/api/v1/admin/users/{legacy.id}", json={"plan_until": later}, headers=ha).json()["plan_source"] == "manual"
    assert (legacy.plan, legacy.plan_until.isoformat()) == ("PRO_PLUS", later)
    from instilens.api.main import app

    app.dependency_overrides.clear()
