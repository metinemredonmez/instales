"""Subscriptions and payments: who granted which plan, and the payment provider behind it.

Two providers write the `subscriptions` table. StripeProvider talks to Stripe through the official SDK only — a
Checkout Session in subscription mode for an upgrade, a Billing Portal session for "manage billing", and the
signed webhook for everything that happens afterwards; it is inert (checkout / portal raise NotConfigured, the
routes answer 503) until the secret key, the webhook signing secret and both price ids are set. ManualProvider
records what an admin granted from the Users page (a plan, an optional expiry, a note). Nothing here invents a
price: what the plan page shows comes from Stripe's Price objects, or is null.

Webhook events are applied by `apply_event`, once per event id (`processed_webhooks`): a redelivery is
acknowledged and changes nothing, and an event older than the last one applied to its subscription
(`provider_event_at`) is skipped — Stripe does not deliver in order. The user's plan follows the subscription — set
once checkout.session.completed is paid or customer.subscription.updated says active, kept while a renewal is
PAST_DUE (Stripe retries), dropped to FREE on customer.subscription.deleted (or paused). One live paid subscription
per account: a second checkout is refused (SubscriptionExists → 409) and a plan change goes through the Billing
Portal's subscription_update flow on the existing subscription, so nobody is billed twice. The provider is
process-wide (`provider()`); tests swap in a fake with `use_provider`.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.enums import Plan, SubscriptionStatus
from instilens.domain.models import Organization, ProcessedWebhook, Subscription, User
from instilens.services import plans

log = logging.getLogger("instilens.billing")

PRICE_CACHE_S = 3600.0  # a Price object re-read from Stripe at most hourly (the plan page asks on every visit)
_STRIPE_STATUS = {  # Stripe subscription.status → SubscriptionStatus; anything unlisted reads as INCOMPLETE
    "trialing": SubscriptionStatus.TRIALING, "active": SubscriptionStatus.ACTIVE,
    "past_due": SubscriptionStatus.PAST_DUE, "unpaid": SubscriptionStatus.PAST_DUE,
    "canceled": SubscriptionStatus.CANCELED, "incomplete_expired": SubscriptionStatus.CANCELED,
    "incomplete": SubscriptionStatus.INCOMPLETE, "paused": SubscriptionStatus.INCOMPLETE,
}
LIVE = (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE)
PAID_STATUSES = ("paid", "no_payment_required")  # Checkout Session payment_status values that grant the plan at once
PORTAL_FLOWS = ("subscription_update",)  # Billing Portal flow_data types the portal route accepts


class BillingError(Exception):
    pass


class NotConfigured(BillingError):
    """No provider keys: the routes answer 503 "payments not configured", the UI its calm empty state."""


class NoBillingAccount(BillingError):
    """The user never checked out, so the provider knows no customer to open a portal for."""


class SubscriptionExists(BillingError):
    """A paid subscription is already live for the account (409): a plan change goes through the portal, not a
    second checkout — Stripe would happily attach a second subscription to the customer and invoice both."""


class WebhookError(BillingError):
    """Bad signature or unreadable payload → 400 (Stripe retries a 400 as much as a 500; the log says why)."""


@dataclass(frozen=True)
class WebhookEvent:
    """One provider event as plain JSON: `object` is the event's data.object (a Checkout Session, a Subscription,
    an Invoice), untouched, so the handlers read exactly what the provider sent."""

    id: str
    type: str
    object: dict[str, Any]
    created: int | None = None  # the provider's event timestamp (unix seconds), for ordering; None = unknown


class PaymentProvider(Protocol):
    name: str

    def configured(self) -> bool: ...

    def price(self, plan: str) -> dict | None:
        """{"id", "amount", "currency", "interval", "tax_behavior"} of the plan's recurring price, `amount` null when unknown; None when unconfigured."""

    def checkout_session(self, session: Session, user: User, plan: str, success_url: str, cancel_url: str) -> str:
        """URL of a hosted checkout for `plan`; NotConfigured / BillingError otherwise."""

    def portal_session(self, session: Session, user: User, return_url: str, flow: str | None = None) -> str:
        """URL of the provider's self-service billing page for the user's customer; `flow` (PORTAL_FLOWS) opens it
        on one task — "subscription_update" lands on the plan picker of the live subscription."""

    def handle_webhook(self, session: Session, payload: bytes, sig: str | None) -> dict:
        """Verify and apply one webhook delivery (WebhookError on a bad signature); returns what `apply_event` did."""


# ---------------------------------------------------------------- Stripe
class StripeProvider:
    """The official `stripe` SDK behind the PaymentProvider protocol. Every call reads the keys as they are now, so
    a key added to .env takes effect on the next process start without touching this object."""

    name = "stripe"

    def __init__(self) -> None:
        self._client_key: str | None = None
        self._client: Any = None
        self._prices: dict[str, tuple[float, dict]] = {}  # price id → (monotonic time read, payload)

    def configured(self) -> bool:
        return bool(settings.stripe_secret_key and settings.stripe_webhook_secret and settings.stripe_price_pro and settings.stripe_price_pro_plus)

    def _api(self):
        import stripe

        if self._client is None or self._client_key != settings.stripe_secret_key:
            self._client = stripe.StripeClient(settings.stripe_secret_key)
            self._client_key = settings.stripe_secret_key
        return self._client

    @staticmethod
    def price_id(plan: str) -> str | None:
        return {Plan.PRO.value: settings.stripe_price_pro, Plan.PRO_PLUS.value: settings.stripe_price_pro_plus}.get(plan)

    def price(self, plan: str) -> dict | None:
        """The Price object as Stripe states it (unit_amount is in the smallest unit: cents → 2 decimals). A read
        that fails answers `amount: null` and is retried on the next visit — never a made-up number."""
        price_id = self.price_id(plan)
        if not self.configured() or not price_id:
            return None
        cached = self._prices.get(price_id)
        if cached and time.monotonic() - cached[0] < PRICE_CACHE_S:
            return cached[1]
        import stripe

        try:
            p = self._api().v1.prices.retrieve(price_id)
            recurring = p.recurring
            # tax_behavior is the Price's own word on whether the amount includes tax: inclusive / exclusive / unspecified.
            out = {"id": price_id, "amount": p.unit_amount / 100 if p.unit_amount is not None else None, "currency": p.currency,
                   "interval": recurring.interval if recurring else None, "tax_behavior": getattr(p, "tax_behavior", None) or "unspecified"}
        except stripe.StripeError as exc:
            log.warning("stripe price %s unreadable: %s", price_id, exc)
            return {"id": price_id, "amount": None, "currency": settings.billing_currency, "interval": None, "tax_behavior": "unspecified"}
        self._prices[price_id] = (time.monotonic(), out)
        return out

    def checkout_session(self, session: Session, user: User, plan: str, success_url: str, cancel_url: str) -> str:
        """Checkout Session, mode=subscription, one line item (the plan's recurring price). The user id and plan
        ride along as metadata on the session and on the subscription it creates, which is how the webhook maps
        the provider's objects back to the account; an existing customer is reused, otherwise Stripe creates one
        for the e-mail. Refused (SubscriptionExists) while a subscription of this provider is live — a plan change
        is a portal flow on that subscription. Tax and terms consent are added when the deployment opted in
        (settings; both need dashboard setup Stripe checks on its side)."""
        import stripe

        price_id = self.price_id(plan)
        if not self.configured() or not price_id:
            raise NotConfigured("payments not configured")
        if live_subscription(session, user, self.name) is not None:
            raise SubscriptionExists("subscription_exists")
        meta = {"user_id": str(user.id), "plan": plan}
        params: dict[str, Any] = {
            "mode": "subscription", "line_items": [{"price": price_id, "quantity": 1}],
            "success_url": success_url, "cancel_url": cancel_url,
            "client_reference_id": str(user.id), "metadata": meta, "subscription_data": {"metadata": meta},
            "allow_promotion_codes": True,
        }
        if settings.stripe_automatic_tax:
            params["automatic_tax"] = {"enabled": True}
        if settings.stripe_terms_consent:
            params["consent_collection"] = {"terms_of_service": "required"}
        customer = customer_id(session, user)
        if customer:
            params["customer"] = customer
        else:
            params["customer_email"] = user.email
        try:
            return self._api().v1.checkout.sessions.create(params=params).url
        except stripe.StripeError as exc:
            log.warning("stripe checkout failed for user %s: %s", user.id, exc)
            raise BillingError("payment provider error") from exc

    def portal_session(self, session: Session, user: User, return_url: str, flow: str | None = None) -> str:
        import stripe

        if not self.configured():
            raise NotConfigured("payments not configured")
        customer = customer_id(session, user)
        if not customer:
            raise NoBillingAccount("no subscription to manage")
        params: dict[str, Any] = {"customer": customer, "return_url": return_url}
        if flow == "subscription_update":  # the portal's plan picker for the live subscription: a change, never a second one
            live = live_subscription(session, user, self.name)
            if live is None or not live.provider_subscription_id:
                raise NoBillingAccount("no subscription to change")
            params["flow_data"] = {"type": "subscription_update", "subscription_update": {"subscription": live.provider_subscription_id}}
        try:
            return self._api().v1.billing_portal.sessions.create(params=params).url
        except stripe.StripeError as exc:
            log.warning("stripe portal failed for user %s: %s", user.id, exc)
            raise BillingError("payment provider error") from exc

    def handle_webhook(self, session: Session, payload: bytes, sig: str | None) -> dict:
        """`stripe.Webhook.construct_event` checks the signature (and the 5-minute timestamp tolerance) against the
        endpoint's signing secret; the verified body is then read as plain JSON for the handlers."""
        import stripe

        if not settings.stripe_webhook_secret:
            raise NotConfigured("payments not configured")
        try:
            event = stripe.Webhook.construct_event(payload, sig, settings.stripe_webhook_secret)
        except stripe.SignatureVerificationError as exc:
            raise WebhookError("invalid signature") from exc
        except ValueError as exc:
            raise WebhookError("invalid payload") from exc
        raw = json.loads(payload)
        return apply_event(session, WebhookEvent(id=event.id, type=event.type, object=raw.get("data", {}).get("object") or {}, created=getattr(event, "created", None)), provider=self.name)


# ---------------------------------------------------------------- manual (admin) grants
class ManualProvider:
    """What the admin Users page hands out. Not a PaymentProvider — there is nothing to check out — but the same
    table: one live row per user at a time, the earlier one CANCELED when a new grant replaces it. A paid plan is
    never granted over a live paid subscription (BillingError): it would either be silently undone by the next
    renewal event or outlive a cancelled payment — the provider is where it changes. Lifting a grant is always
    allowed; the paid subscription, if any, is what the account falls back to."""

    name = "manual"

    def grant(self, session: Session, user: User, plan: str, *, until: date | None = None, note: str | None = None, actor: str | None = None) -> Subscription | None:
        """Set the account's own plan by hand. FREE revokes: the live manual row is CANCELED and the account falls
        back to its paid subscription if one is live, else to FREE with the source cleared. A paid plan cannot be
        granted over a live paid subscription (BillingError: it changes at the provider). Returns the row written
        (None for a revocation)."""
        paid = live_subscription(session, user, _provider.name)
        if plan != Plan.FREE.value and paid is not None:
            raise BillingError(f"managed by {paid.provider} — cancel or change it there")
        now = datetime.now(UTC)
        for row in session.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.provider == self.name, Subscription.status.in_(LIVE))):
            row.status, row.updated_at = SubscriptionStatus.CANCELED.value, now
        if plan == Plan.FREE.value:
            user.plan, user.plan_source, user.plan_until = (paid.plan, paid.provider, None) if paid else (plan, None, None)
            _owner_plan_changed(session, user)
            return None
        text = f"{actor}: {note}" if actor and note else (note or actor)
        row = Subscription(user_id=user.id, plan=plan, status=SubscriptionStatus.ACTIVE.value, provider=self.name,
                           current_period_end=datetime.combine(until, datetime.max.time()) if until else None, note=(text or "")[:256] or None)
        session.add(row)
        user.plan, user.plan_source, user.plan_until = plan, self.name, until
        _owner_plan_changed(session, user)
        session.flush()
        return row

    def redate(self, session: Session, user: User, until: date | None) -> None:
        """Move (or lift, with None) the expiry of the account's live manual grant — the admin page's "manual until"
        field on its own. The user row and the live manual subscription row change in place; nothing new is written.
        A plan set before grants were recorded (no `plan_source`) is adopted as a manual one on its first re-date;
        an account whose plan is its paid subscription has nothing manual to date."""
        if user.plan == Plan.FREE.value or user.plan_source not in (self.name, None):
            paid = live_subscription(session, user, _provider.name)
            raise BillingError(f"managed by {paid.provider} — cancel or change it there" if paid else "no manual grant to date")
        if user.plan_source is None and live_subscription(session, user, _provider.name) is not None:
            raise BillingError("no manual grant to date")
        user.plan_source, user.plan_until = self.name, until
        end = datetime.combine(until, datetime.max.time()) if until else None
        for row in session.scalars(select(Subscription).where(Subscription.user_id == user.id, Subscription.provider == self.name, Subscription.status.in_(LIVE))):
            row.current_period_end, row.updated_at = end, datetime.now(UTC)
        _owner_plan_changed(session, user)
        session.flush()


# ---------------------------------------------------------------- the process-wide provider
_provider: PaymentProvider = StripeProvider()
manual = ManualProvider()


def provider() -> PaymentProvider:
    return _provider


def use_provider(p: PaymentProvider | None) -> None:
    """Swap the provider (tests). None = Stripe."""
    global _provider
    _provider = p if p is not None else StripeProvider()


def configured() -> bool:
    return _provider.configured()


# ---------------------------------------------------------------- reads
def customer_id(session: Session, user: User) -> str | None:
    """The provider's customer for this user, from the newest subscription row that carries one."""
    return session.scalar(
        select(Subscription.provider_customer_id)
        .where(Subscription.user_id == user.id, Subscription.provider == _provider.name, Subscription.provider_customer_id.is_not(None))
        .order_by(Subscription.id.desc()).limit(1)
    )


def current_subscription(session: Session, user: User) -> Subscription | None:
    """The user's live subscription row (any provider), else the newest one; None when nothing was ever granted."""
    live = session.scalar(select(Subscription).where(Subscription.user_id == user.id, Subscription.status.in_(LIVE)).order_by(Subscription.id.desc()).limit(1))
    return live or session.scalar(select(Subscription).where(Subscription.user_id == user.id).order_by(Subscription.id.desc()).limit(1))


def live_subscription(session: Session, user: User, provider_name: str, *, exclude_id: int | None = None) -> Subscription | None:
    """The user's live row of one provider (the newest), if any — what a checkout, a portal flow and a manual grant
    check first."""
    stmt = select(Subscription).where(Subscription.user_id == user.id, Subscription.provider == provider_name, Subscription.status.in_(LIVE))
    if exclude_id is not None:
        stmt = stmt.where(Subscription.id != exclude_id)
    return session.scalar(stmt.order_by(Subscription.id.desc()).limit(1))


def subscription_json(sub: Subscription | None) -> dict | None:
    """The row as /billing/me shows it to its owner. The manual `note` (who granted it and why) stays with the admin
    pages and the audit trail — it names a third party and is written for staff, not for the customer."""
    if sub is None:
        return None
    return {"id": sub.id, "plan": sub.plan, "status": sub.status, "provider": sub.provider,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
            "cancel_at_period_end": bool(sub.cancel_at_period_end), "created_at": sub.created_at.isoformat(), "updated_at": sub.updated_at.isoformat()}


def plans_payload() -> dict:
    """/billing/plans: the matrix with each paid tier's price as the provider states it."""
    p = _provider
    return {
        "plans": [{"code": code, "features": plans.features_of(code), "price": p.price(code) if code in plans.PAID_PLANS else None} for code in plans.PLANS],
        "features": list(plans.FEATURES),
        "currency": settings.billing_currency,
        "configured": p.configured(),
        "provider": p.name,
        "plans_enforced": bool(settings.plans_enforced),
    }


def me_payload(session: Session, user: User) -> dict:
    """/billing/me: the effective plan and its source, the subscription row behind it, the organisation if any."""
    out = plans.me_payload(session, user)
    orgs = plans.memberships(session, user)
    org = orgs[0] if orgs else None
    return {**out, "source": out["plan_source"], "subscription": subscription_json(current_subscription(session, user)),
            "org": {"id": org.id, "name": org.name, "plan": org.plan, "owner": org.owner_user_id == user.id} if org else None,
            "configured": _provider.configured(), "provider": _provider.name}


# ---------------------------------------------------------------- applying provider events
def apply_event(session: Session, event: WebhookEvent, provider: str = "stripe") -> dict:
    """Apply one provider event exactly once. The `processed_webhooks` row is written first, inside a savepoint:
    a redelivery (or the same event on two workers at once) hits the unique key and is answered as a duplicate."""
    try:
        with session.begin_nested():
            session.add(ProcessedWebhook(provider=provider, event_id=event.id, event_type=event.type[:64]))
            session.flush()
    except IntegrityError:
        log.info("webhook %s %s already applied", provider, event.id)
        return {"applied": False, "duplicate": True, "type": event.type}
    handler = _HANDLERS.get(event.type)
    if handler is None:
        return {"applied": False, "ignored": True, "type": event.type}
    out = handler(session, event.object, provider, _when(event.created))
    session.flush()
    log.info("webhook %s %s %s: %s", provider, event.id, event.type, out)
    return {"applied": True, "type": event.type, **out}


def _id(value: Any) -> str | None:
    """An expandable Stripe field: the id string, or the expanded object's id."""
    if isinstance(value, dict):
        return value.get("id")
    return str(value) if value else None


def _when(ts: Any) -> datetime | None:
    try:
        return datetime.fromtimestamp(int(ts), UTC) if ts else None
    except (TypeError, ValueError, OSError):
        return None


def _period_end(obj: dict) -> datetime | None:
    """`current_period_end` sits on the subscription up to API version 2025-02, on each item from 2025-03-31 on."""
    if obj.get("current_period_end"):
        return _when(obj["current_period_end"])
    items = ((obj.get("items") or {}).get("data")) or []
    return _when(items[0].get("current_period_end")) if items else None


def _plan_of(obj: dict) -> str | None:
    """The plan a provider object stands for: the price it carries, else its metadata. The price is what the
    customer is billed for and changes with a portal plan switch; `metadata.plan` is written once at checkout and
    never again, so it only speaks for a Checkout Session (which carries no items)."""
    items = ((obj.get("items") or {}).get("data")) or []
    for item in items:
        price = _id(item.get("price"))
        for plan in plans.PAID_PLANS:
            if price and price == StripeProvider.price_id(plan):
                return plan
    meta = obj.get("metadata") or {}
    if not items and meta.get("plan") in plans.PAID_PLANS:
        return meta["plan"]
    return None


def _user_of(session: Session, obj: dict) -> User | None:
    meta = obj.get("metadata") or {}
    ref = meta.get("user_id") or obj.get("client_reference_id")
    if not ref or not str(ref).isdigit():
        return None
    return session.get(User, int(ref))


def _row(session: Session, provider: str, subscription_id: str | None) -> Subscription | None:
    if not subscription_id:
        return None
    return session.scalar(select(Subscription).where(Subscription.provider == provider, Subscription.provider_subscription_id == subscription_id))


def _retire_others(session: Session, keep: Subscription) -> None:
    """One live paid row per user: an earlier row of the same provider is CANCELED, and so is a manual grant the
    paid plan covers. A manual grant of a *higher* plan stays — an admin's PRO_PLUS is not undone by paying for PRO."""
    for row in session.scalars(select(Subscription).where(Subscription.user_id == keep.user_id, Subscription.id != keep.id, Subscription.status.in_(LIVE))):
        if row.provider == keep.provider or plans.RANK.get(row.plan, 0) <= plans.RANK.get(keep.plan, 0):
            row.status, row.updated_at = SubscriptionStatus.CANCELED.value, datetime.now(UTC)


def _stale(sub: Subscription, at: datetime | None) -> bool:
    """Whether an event dated `at` is older than the last one applied to the row (webhooks arrive in any order)."""
    return at is not None and sub.provider_event_at is not None and at < sub.provider_event_at.replace(tzinfo=UTC)


def _apply_to_user(session: Session, sub: Subscription) -> None:
    """The account follows its subscription: the plan while it is live (PAST_DUE included — the provider is still
    retrying), unless a manual grant of a higher plan is in force; once it is no longer live (CANCELED, paused,
    never paid) the account falls back to another live subscription of the same provider, else FREE. A plan that
    came from elsewhere (an admin grant) is left alone."""
    user = session.get(User, sub.user_id) if sub.user_id else None
    if user is None:
        return
    if sub.status in LIVE:
        if user.plan_source == sub.provider or plans.RANK.get(sub.plan, 0) >= plans.RANK[plans.own_plan(user)]:
            user.plan, user.plan_source, user.plan_until = sub.plan, sub.provider, None
        _retire_others(session, sub)
    elif user.plan_source == sub.provider:
        other = live_subscription(session, user, sub.provider, exclude_id=sub.id)
        user.plan, user.plan_source, user.plan_until = (other.plan, other.provider, None) if other else (Plan.FREE.value, None, None)
    _owner_plan_changed(session, user)


def _owner_plan_changed(session: Session, user: User) -> None:
    """Keep the mirror columns of an organisation this user owns in step (its members read the live value anyway)."""
    org = session.scalar(select(Organization).where(Organization.owner_user_id == user.id))
    if org is not None:
        plans.refresh_org(session, org)


def _checkout_completed(session: Session, obj: dict, provider: str, at: datetime | None) -> dict:
    if obj.get("mode") != "subscription":
        return {"ignored": True, "reason": "not a subscription checkout"}
    user, plan = _user_of(session, obj), _plan_of(obj)
    if user is None or plan is None:
        return {"ignored": True, "reason": "unknown user or plan"}
    sub_id = _id(obj.get("subscription"))
    sub = _row(session, provider, sub_id)
    now = datetime.now(UTC)
    if sub is None:
        sub = Subscription(user_id=user.id, plan=plan, status=SubscriptionStatus.INCOMPLETE.value, provider=provider, provider_subscription_id=sub_id, created_at=now)
        session.add(sub)
    elif _stale(sub, at):
        return {"ignored": True, "reason": "stale event"}
    sub.user_id, sub.plan, sub.updated_at = user.id, plan, now
    sub.provider_customer_id = _id(obj.get("customer")) or sub.provider_customer_id
    # Paid (or nothing to pay, a 100% coupon) grants the plan now. A delayed payment method leaves the session
    # unpaid: the row stays INCOMPLETE and the plan waits for the subscription event that says active.
    if obj.get("payment_status") in PAID_STATUSES:
        sub.status = SubscriptionStatus.ACTIVE.value
    sub.provider_event_at = at or sub.provider_event_at
    session.flush()
    _apply_to_user(session, sub)
    return {"user_id": user.id, "plan": plan, "status": sub.status}


def _subscription_changed(session: Session, obj: dict, provider: str, at: datetime | None) -> dict:
    sub_id = _id(obj.get("id"))
    sub = _row(session, provider, sub_id)
    status = _STRIPE_STATUS.get(str(obj.get("status")), SubscriptionStatus.INCOMPLETE)
    if sub is None:  # customer.subscription.created can arrive before checkout.session.completed
        user, plan = _user_of(session, obj), _plan_of(obj)
        if user is None:
            return {"ignored": True, "reason": "unknown subscription"}
        if plan is None:  # a price that is not one of ours grants nothing here
            return {"ignored": True, "reason": "unknown plan"}
        sub = Subscription(user_id=user.id, plan=plan, status=status.value, provider=provider, provider_subscription_id=sub_id)
        session.add(sub)
    elif _stale(sub, at):
        # Status and plan are the newer event's; what the row still lacks (the period end, the customer — a
        # subscription.created that arrives just after its checkout) is worth keeping from an older one.
        sub.current_period_end = sub.current_period_end or _period_end(obj)
        sub.provider_customer_id = sub.provider_customer_id or _id(obj.get("customer"))
        session.flush()
        return {"ignored": True, "reason": "stale event"}
    sub.status = status.value
    sub.plan = _plan_of(obj) or sub.plan
    sub.provider_customer_id = _id(obj.get("customer")) or sub.provider_customer_id
    sub.current_period_end = _period_end(obj) or sub.current_period_end
    sub.cancel_at_period_end = bool(obj.get("cancel_at_period_end"))
    sub.provider_event_at = at or sub.provider_event_at
    sub.updated_at = datetime.now(UTC)
    session.flush()
    _apply_to_user(session, sub)
    return {"user_id": sub.user_id, "plan": sub.plan, "status": sub.status}


def _subscription_deleted(session: Session, obj: dict, provider: str, at: datetime | None) -> dict:
    sub = _row(session, provider, _id(obj.get("id")))
    if sub is None:
        return {"ignored": True, "reason": "unknown subscription"}
    if _stale(sub, at):
        return {"ignored": True, "reason": "stale event"}
    sub.status, sub.cancel_at_period_end, sub.updated_at = SubscriptionStatus.CANCELED.value, False, datetime.now(UTC)
    sub.current_period_end = _period_end(obj) or sub.current_period_end
    sub.provider_event_at = at or sub.provider_event_at
    session.flush()
    _apply_to_user(session, sub)
    return {"user_id": sub.user_id, "plan": sub.plan, "status": sub.status}


def _payment_failed(session: Session, obj: dict, provider: str, at: datetime | None) -> dict:
    """An invoice's subscription: a top-level `subscription` up to API version 2025-02, `parent.subscription_details`
    from 2025-03-31 on. The plan is kept — Stripe keeps retrying and reports the outcome as a subscription event."""
    sub_id = _id(obj.get("subscription")) or _id(((obj.get("parent") or {}).get("subscription_details") or {}).get("subscription"))
    sub = _row(session, provider, sub_id)
    if sub is None:
        return {"ignored": True, "reason": "unknown subscription"}
    if _stale(sub, at):
        return {"ignored": True, "reason": "stale event"}
    if sub.status in LIVE:
        sub.status, sub.updated_at = SubscriptionStatus.PAST_DUE.value, datetime.now(UTC)
    sub.provider_event_at = at or sub.provider_event_at
    session.flush()
    _apply_to_user(session, sub)
    return {"user_id": sub.user_id, "plan": sub.plan, "status": sub.status}


_HANDLERS = {
    "checkout.session.completed": _checkout_completed,
    "customer.subscription.created": _subscription_changed,
    "customer.subscription.updated": _subscription_changed,
    "customer.subscription.deleted": _subscription_deleted,
    "invoice.payment_failed": _payment_failed,
}
