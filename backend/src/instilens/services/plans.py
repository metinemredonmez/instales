"""Plans: the feature matrix, the plan an account actually gets, and the gate.

Three tiers (domain.enums.Plan). FEATURES is the whole contract — a bool is on/off, an int is a cap (0 = off) —
and /auth/me and /billing/plans hand it to the UI as it stands here, so a limit is never restated elsewhere.

Where a user's plan comes from: the account's own `users.plan` (FREE unless a Stripe subscription or an admin grant
set it; a grant with `plan_until` in the past reads as FREE), or an organisation the user is an accepted member of,
whose plan is its owner's own plan (services/org) — the higher of the two wins (`effective_plan`).

Gating is a runtime switch (`settings.plans_enforced`, admin-editable, off by default): while it is off every
account works as before and this module only *describes* limits. When it is on, `require` / `enforce_limit` raise
PlanLimit, which the API answers as 402 {"detail": "plan_limit", "feature", "plan", "limit", "upgrade"}. ADMIN accounts
are never gated. Existing rows above a cap are never touched — a cap stops the next addition, nothing else.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.enums import Plan, SubscriptionStatus
from instilens.domain.models import Organization, OrgMember, Subscription, User

PLANS: tuple[str, ...] = tuple(p.value for p in Plan)  # tier order
RANK = {p: i for i, p in enumerate(PLANS)}
PAID_PLANS: tuple[str, ...] = (Plan.PRO.value, Plan.PRO_PLUS.value)
SOURCES = ("own", "org", "manual")  # what /billing/me and /auth/me report as `plan_source`

# feature → value per plan. Caps are per owner (alert_rules, portfolios, ai_research_per_day), per watchlist
# (watchlist_items), per portfolio (portfolio_positions) or per organisation (org_seats, owner included). Only what
# the product enforces or delivers is listed: the matrix is what the plan page sells, so a row here is a promise.
FEATURES: dict[str, dict[str, bool | int]] = {
    "watchlist_items": {Plan.FREE: 10, Plan.PRO: 100, Plan.PRO_PLUS: 500},
    "alert_rules": {Plan.FREE: 3, Plan.PRO: 50, Plan.PRO_PLUS: 500},
    "ai_research_per_day": {Plan.FREE: 5, Plan.PRO: 50, Plan.PRO_PLUS: 300},
    "briefs": {Plan.FREE: True, Plan.PRO: True, Plan.PRO_PLUS: True},  # reading the morning brief; every plan
    "portfolio": {Plan.FREE: False, Plan.PRO: True, Plan.PRO_PLUS: True},
    "portfolios": {Plan.FREE: 0, Plan.PRO: 1, Plan.PRO_PLUS: 5},
    "portfolio_positions": {Plan.FREE: 0, Plan.PRO: 100, Plan.PRO_PLUS: 500},
    "tts": {Plan.FREE: False, Plan.PRO: True, Plan.PRO_PLUS: True},  # narrated notes and briefs
    "push": {Plan.FREE: False, Plan.PRO: True, Plan.PRO_PLUS: True},  # web push devices
    "org_seats": {Plan.FREE: 0, Plan.PRO: 0, Plan.PRO_PLUS: 10},
}
_LIVE_STATUSES = (SubscriptionStatus.ACTIVE, SubscriptionStatus.TRIALING, SubscriptionStatus.PAST_DUE)


class PlanLimit(Exception):
    """A gated feature or an exhausted cap; `detail()` is the 402 body."""

    def __init__(self, feature: str, plan: str, limit: int, upgrade: str) -> None:
        super().__init__(f"{feature} needs a higher plan than {plan} (limit {limit})")
        self.feature, self.plan, self.limit, self.upgrade = feature, plan, limit, upgrade

    def detail(self) -> dict:
        return {"detail": "plan_limit", "feature": self.feature, "plan": self.plan, "limit": self.limit, "upgrade": self.upgrade}


# ---------------------------------------------------------------- the matrix
def features_of(plan: str) -> dict[str, bool | int]:
    plan = plan if plan in RANK else Plan.FREE.value
    return {feature: values[plan] for feature, values in FEATURES.items()}


def matrix() -> dict[str, dict[str, bool | int]]:
    """{plan: {feature: value}} in tier order — the plan page's table."""
    return {p: features_of(p) for p in PLANS}


def upgrade_for(feature: str, plan: str) -> str:
    """The lowest tier above `plan` that gives more of `feature`; PRO_PLUS when none does (the top of the ladder)."""
    current = FEATURES[feature][plan]
    for candidate in PLANS[RANK[plan] + 1:]:
        if FEATURES[feature][candidate] > current:
            return candidate
    return Plan.PRO_PLUS.value


# ---------------------------------------------------------------- resolution
def own_plan(user: User, today: date | None = None) -> str:
    """The plan on the account itself; FREE once a dated grant has run out (the row is left as it is)."""
    if user.plan not in RANK:
        return Plan.FREE.value
    if user.plan_until is not None and user.plan_until < (today or date.today()):
        return Plan.FREE.value
    return user.plan


def org_plan(session: Session, org: Organization, today: date | None = None) -> str:
    """What the organisation's members inherit: the owner's own plan, or a live subscription granted to the
    organisation itself, whichever is higher. Resolved live so the owner's expiry or cancellation reaches the
    members at once; `organizations.plan` / `seats` are refreshed from it whenever the organisation is read."""
    owner = session.get(User, org.owner_user_id)
    best = own_plan(owner, today) if owner is not None and owner.is_active else Plan.FREE.value
    for sub in session.scalars(select(Subscription).where(Subscription.org_id == org.id, Subscription.status.in_(_LIVE_STATUSES))):
        if sub.plan in RANK and RANK[sub.plan] > RANK[best] and (sub.current_period_end is None or sub.current_period_end.date() >= (today or date.today())):
            best = sub.plan
    return best


def refresh_org(session: Session, org: Organization, today: date | None = None) -> str:
    """Bring the mirror columns up to date and return the organisation's plan."""
    plan = org_plan(session, org, today)
    seats = int(FEATURES["org_seats"][plan])
    if org.plan != plan or org.seats != seats:
        org.plan, org.seats = plan, seats
    return plan


def memberships(session: Session, user: User) -> list[Organization]:
    """The organisations the user is an accepted member of (owner included), oldest first."""
    return session.scalars(
        select(Organization).join(OrgMember, OrgMember.org_id == Organization.id)
        .where(OrgMember.user_id == user.id, OrgMember.accepted_at.is_not(None)).order_by(Organization.id)
    ).all()


def effective_plan(session: Session, user: User, today: date | None = None) -> tuple[str, str]:
    """(plan, source): the higher of the account's own plan and its organisations' plans. Source is "org" when an
    organisation lifts the account above its own plan, "manual" for an admin grant, else "own"."""
    own = own_plan(user, today)
    best, source = own, ("manual" if own != Plan.FREE and user.plan_source == "manual" else "own")
    for org in memberships(session, user):
        plan = refresh_org(session, org, today)
        if RANK[plan] > RANK[best]:
            best, source = plan, "org"
    return best, source


def features(session: Session, user: User) -> dict[str, bool | int]:
    return features_of(effective_plan(session, user)[0])


# ---------------------------------------------------------------- the gate
def enforced(user: User) -> bool:
    """Whether this account is gated at all: the runtime switch, and never for ADMIN."""
    return bool(settings.plans_enforced) and user.role != "ADMIN"


def _user(session: Session, user: User | str | int) -> User | None:
    """The account behind a User, an id or an owner_id string (`str(user.id)` everywhere in the user layer)."""
    if isinstance(user, User):
        return user
    return session.get(User, int(user)) if str(user).isdigit() else None


def limit(session: Session, user: User | str | int, feature: str) -> int | None:
    """The cap that applies to this account for an int feature; None = no cap (gating off, ADMIN, unknown user)."""
    u = _user(session, user)
    if u is None or not enforced(u):
        return None
    value = features(session, u)[feature]
    return int(value)


def allows(session: Session, user: User | str | int, feature: str) -> bool:
    """Whether the account may use a feature at all (a bool feature, or an int feature with a cap above zero)."""
    u = _user(session, user)
    if u is None or not enforced(u):
        return True
    return bool(features(session, u)[feature])


def require(session: Session, user: User, feature: str) -> None:
    """Gate a whole feature (api/deps.require_plan): PlanLimit when the plan does not include it."""
    if not enforced(user):
        return
    plan = effective_plan(session, user)[0]
    value = FEATURES[feature][plan]
    if not value:
        raise PlanLimit(feature, plan, int(value), upgrade_for(feature, plan))


def enforce_limit(session: Session, user: User | str | int, feature: str, used: int) -> None:
    """Gate one more of a capped thing: PlanLimit when `used` (what the owner already has) has reached the cap."""
    u = _user(session, user)
    if u is None or not enforced(u):
        return
    plan = effective_plan(session, u)[0]
    cap = int(FEATURES[feature][plan])
    if used >= cap:
        raise PlanLimit(feature, plan, cap, upgrade_for(feature, plan))


def me_payload(session: Session, user: User) -> dict:
    """The plan block of /auth/me: the effective plan, where it comes from, the matrix that applies, the switch."""
    plan, source = effective_plan(session, user)
    return {"plan": plan, "plan_source": source, "own_plan": own_plan(user), "plan_until": user.plan_until.isoformat() if user.plan_until else None,
            "features": features_of(plan), "plans_enforced": bool(settings.plans_enforced)}
