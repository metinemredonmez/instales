"""Closed vocabularies shared by the schema, the engines and the API.

Everything the product says ("ADD", "GROUPED", "ACCUMULATION") must be one of these values,
so the UI, the methodology docs and the database never drift apart.
"""

from enum import StrEnum


class Market(StrEnum):
    TR = "TR"
    US = "US"


class Source(StrEnum):
    KAP = "KAP"
    SEC = "SEC"
    TEFAS = "TEFAS"
    MARKET_DATA = "MARKET_DATA"


class DisclosureKind(StrEnum):
    KAP_SHARE_TRANSACTION = "KAP_SHARE_TRANSACTION"  # Pay Alım Satım Bildirimi
    KAP_PORTFOLIO_REPORT = "KAP_PORTFOLIO_REPORT"  # Fon Portföy Dağılım Raporu
    SEC_13F = "SEC_13F"  # global, phase 2
    SEC_FORM4 = "SEC_FORM4"  # global, phase 2


class Confidence(StrEnum):
    """How sure we are about *who* did *how much*.

    EXACT    — one fund, one instrument, explicit amount.
    GROUPED  — explicit amount but attributed to a set of related funds; per-fund split unknown.
    INFERRED — derived by diffing two portfolio snapshots; timing inside the period unknown.
    """

    EXACT = "EXACT"
    GROUPED = "GROUPED"
    INFERRED = "INFERRED"


# Score multiplier per confidence level. Deliberately milder than the 1.0/0.7/0.6 first draft:
# most of the dataset is INFERRED (snapshot diffs), which is coarse in *timing*, not wrong in
# *amount*. A 0.6 cap would make the score structurally unable to exceed 60 for most stocks.
CONFIDENCE_MULTIPLIER: dict[Confidence, float] = {
    Confidence.EXACT: 1.0,
    Confidence.GROUPED: 0.9,
    Confidence.INFERRED: 0.8,
}


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"
    MIXED = "MIXED"  # a disclosure containing both buys and sells


class ActivityType(StrEnum):
    """Dataroma-style vocabulary for a fund's move in one instrument between two snapshots."""

    NEW = "NEW"
    ADD = "ADD"
    REDUCE = "REDUCE"
    EXIT = "EXIT"
    HOLD = "HOLD"


class SignalType(StrEnum):
    ACCUMULATION = "ACCUMULATION"
    DISTRIBUTION = "DISTRIBUTION"
    POSITIVE_DIVERGENCE = "POSITIVE_DIVERGENCE"  # price down, funds buying
    NEGATIVE_DIVERGENCE = "NEGATIVE_DIVERGENCE"  # price up, funds selling
    NEW_POSITION_CLUSTER = "NEW_POSITION_CLUSTER"
    EXIT_CLUSTER = "EXIT_CLUSTER"
    INSIDER_BUY_CLUSTER = "INSIDER_BUY_CLUSTER"  # US only: ≥3 insiders with open-market purchases (Form 4 code P) in 30 days


class ScoreType(StrEnum):
    SMART_MONEY = "SMART_MONEY"  # instrument-level
    CONSENSUS = "CONSENSUS"  # instrument-level
    CONVICTION = "CONVICTION"  # fund × instrument
    CROWDING = "CROWDING"  # instrument-level: how many funds hold it and how concentrated (engine/crowding)


class ParseStatus(StrEnum):
    PENDING = "PENDING"
    PARSED = "PARSED"
    FAILED = "FAILED"
    UNSUPPORTED = "UNSUPPORTED"


class InstitutionKind(StrEnum):
    PORTFOLIO_MANAGEMENT_CO = "PORTFOLIO_MANAGEMENT_CO"  # PYŞ
    HEDGE_FUND = "HEDGE_FUND"
    ASSET_MANAGER = "ASSET_MANAGER"
    OTHER = "OTHER"


class PipelineStatus(StrEnum):
    """State of one admin-triggered pipeline run (`pipeline_runs.status`)."""

    RUNNING = "RUNNING"
    OK = "OK"
    ERROR = "ERROR"
    ABANDONED = "ABANDONED"  # a worker died mid-run and the lock was taken over


class AuthTokenKind(StrEnum):
    """What an e-mailed single-use token (`auth_tokens.kind`) unlocks."""

    RESET = "RESET"
    VERIFY = "VERIFY"
    ORG_INVITE = "ORG_INVITE"  # organisation invitation; the token's user is the inviting owner


class Plan(StrEnum):
    """Subscription tiers (`users.plan`, `subscriptions.plan`, `organizations.plan`); the matrix is services/plans.FEATURES."""

    FREE = "FREE"
    PRO = "PRO"
    PRO_PLUS = "PRO_PLUS"


class SubscriptionStatus(StrEnum):
    """`subscriptions.status`, one step away from the provider's vocabulary (services/billing maps Stripe's)."""

    TRIALING = "TRIALING"
    ACTIVE = "ACTIVE"
    PAST_DUE = "PAST_DUE"  # a renewal failed; the plan stays until the provider gives up (CANCELED)
    CANCELED = "CANCELED"
    INCOMPLETE = "INCOMPLETE"  # not (yet) paid for: Stripe's incomplete / paused, an unpaid checkout — grants nothing


class OrgRole(StrEnum):
    OWNER = "OWNER"
    MEMBER = "MEMBER"


class DeliveryChannel(StrEnum):
    """How a notification or a morning brief reached the user (`brief_deliveries.channel`)."""

    PUSH = "push"  # VAPID Web Push
    ONESIGNAL = "onesignal"
    TELEGRAM = "telegram"
    EMAIL = "email"
