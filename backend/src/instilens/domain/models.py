"""SQLAlchemy schema. This is the source of truth for the database.

Design rules (see docs/03-data-model.md):
- every fact row carries its own lineage: `disclosure_id` + `confidence`;
- amounts are integers (nominal/lot) or Numeric (money) — never floats;
- nothing is ever "distributed" across funds unless the source says so
  (`TransactionEventFund.allocated_nominal` is NULL when the split is unknown);
- corrections are modelled with `Disclosure.supersedes_id`; superseded facts stay for audit.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from instilens.domain.enums import PipelineStatus

Money = Numeric(20, 4)
Pct = Numeric(9, 4)


class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- reference data


class MarketRow(Base):
    __tablename__ = "markets"

    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    currency: Mapped[str] = mapped_column(String(8))
    timezone: Mapped[str] = mapped_column(String(64))


class Instrument(Base):
    __tablename__ = "instruments"
    __table_args__ = (UniqueConstraint("market_code", "symbol"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market_code: Mapped[str] = mapped_column(ForeignKey("markets.code"))
    symbol: Mapped[str] = mapped_column(String(16), index=True)
    name: Mapped[str] = mapped_column(String(256))
    isin: Mapped[str | None] = mapped_column(String(12))
    cusip: Mapped[str | None] = mapped_column(String(9), index=True)  # US: 13F rows are keyed by CUSIP
    # False when auto-created by the resolver from an unknown symbol; needs human review.
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True)
    # From the latest fundamentals snapshot (services/fundamentals); lets a holding be read as a share of the company.
    shares_outstanding: Mapped[int | None] = mapped_column(BigInteger)
    shares_as_of: Mapped[date | None] = mapped_column(Date)


class Institution(Base):
    """The disclosing legal entity: a PYŞ in Turkey, a 13F filer in the US."""

    __tablename__ = "institutions"
    __table_args__ = (UniqueConstraint("market_code", "code"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market_code: Mapped[str] = mapped_column(ForeignKey("markets.code"))
    code: Mapped[str] = mapped_column(String(32))
    name: Mapped[str] = mapped_column(String(256))
    kind: Mapped[str] = mapped_column(String(32))
    source_ref: Mapped[str | None] = mapped_column(String(64), index=True)  # KAP member OID / CIK
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True)

    funds: Mapped[list[Fund]] = relationship(back_populates="institution")


class Fund(Base):
    __tablename__ = "funds"

    id: Mapped[int] = mapped_column(primary_key=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"))
    code: Mapped[str] = mapped_column(String(16), unique=True)  # TEFAS code, e.g. TMV
    name: Mapped[str] = mapped_column(String(256))
    fund_type: Mapped[str | None] = mapped_column(String(64))
    isin: Mapped[str | None] = mapped_column(String(12))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=True)

    institution: Mapped[Institution] = relationship(back_populates="funds")


# --------------------------------------------------------------------------- raw layer


class Disclosure(Base):
    """One regulatory filing as received. Raw payload is kept verbatim for re-parsing."""

    __tablename__ = "disclosures"
    __table_args__ = (UniqueConstraint("source", "source_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market_code: Mapped[str] = mapped_column(ForeignKey("markets.code"))
    source: Mapped[str] = mapped_column(String(16))
    source_id: Mapped[str] = mapped_column(String(64))  # KAP disclosure index, SEC accession no.
    kind: Mapped[str] = mapped_column(String(32), index=True)
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id"))
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    raw_uri: Mapped[str | None] = mapped_column(String(512))
    raw_hash: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON)
    supersedes_id: Mapped[int | None] = mapped_column(ForeignKey("disclosures.id"))
    is_superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    parse_status: Mapped[str] = mapped_column(String(16), default="PENDING", index=True)
    parse_error: Mapped[str | None] = mapped_column(Text)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime)


# --------------------------------------------------------------------------- normalized facts


class TransactionEvent(Base):
    """A normalized buy/sell disclosed by an institution (KAP Pay Alım Satım Bildirimi)."""

    __tablename__ = "transaction_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    disclosure_id: Mapped[int] = mapped_column(ForeignKey("disclosures.id"), index=True)
    market_code: Mapped[str] = mapped_column(ForeignKey("markets.code"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    institution_id: Mapped[int] = mapped_column(ForeignKey("institutions.id"), index=True)
    side: Mapped[str] = mapped_column(String(8))
    buy_nominal: Mapped[int] = mapped_column(BigInteger)
    sell_nominal: Mapped[int] = mapped_column(BigInteger)
    net_nominal: Mapped[int] = mapped_column(BigInteger)
    avg_price: Mapped[Decimal | None] = mapped_column(Money)
    net_value: Mapped[Decimal | None] = mapped_column(Money)  # net_nominal × avg_price
    effective_date: Mapped[date] = mapped_column(Date, index=True)  # trade date
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    ownership_before_pct: Mapped[Decimal | None] = mapped_column(Pct)
    ownership_after_pct: Mapped[Decimal | None] = mapped_column(Pct)
    confidence: Mapped[str] = mapped_column(String(16))
    is_superseded: Mapped[bool] = mapped_column(Boolean, default=False)

    funds: Mapped[list[TransactionEventFund]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )


class TransactionEventFund(Base):
    """Which funds a transaction event relates to. `allocated_nominal` is NULL unless EXACT."""

    __tablename__ = "transaction_event_funds"

    event_id: Mapped[int] = mapped_column(ForeignKey("transaction_events.id"), primary_key=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id"), primary_key=True)
    allocated_nominal: Mapped[int | None] = mapped_column(BigInteger)

    event: Mapped[TransactionEvent] = relationship(back_populates="funds")


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (UniqueConstraint("fund_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id"), index=True)
    disclosure_id: Mapped[int | None] = mapped_column(ForeignKey("disclosures.id"))
    as_of: Mapped[date] = mapped_column(Date, index=True)
    total_value: Mapped[Decimal | None] = mapped_column(Money)
    source: Mapped[str] = mapped_column(String(16))
    confidence: Mapped[str] = mapped_column(String(16), default="EXACT")

    holdings: Mapped[list[SnapshotHolding]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class SnapshotHolding(Base):
    __tablename__ = "snapshot_holdings"

    snapshot_id: Mapped[int] = mapped_column(ForeignKey("portfolio_snapshots.id"), primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    quantity: Mapped[int] = mapped_column(BigInteger)
    market_value: Mapped[Decimal | None] = mapped_column(Money)
    weight_pct: Mapped[Decimal | None] = mapped_column(Pct)

    snapshot: Mapped[PortfolioSnapshot] = relationship(back_populates="holdings")


class PositionChange(Base):
    """Fund × instrument delta between two consecutive snapshots. Always INFERRED."""

    __tablename__ = "position_changes"
    __table_args__ = (
        UniqueConstraint("fund_id", "instrument_id", "to_snapshot_id"),
        Index("ix_position_changes_instrument_period", "instrument_id", "period_end"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    fund_id: Mapped[int] = mapped_column(ForeignKey("funds.id"), index=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"))
    from_snapshot_id: Mapped[int | None] = mapped_column(ForeignKey("portfolio_snapshots.id"))
    to_snapshot_id: Mapped[int] = mapped_column(ForeignKey("portfolio_snapshots.id"))
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    from_qty: Mapped[int] = mapped_column(BigInteger)
    to_qty: Mapped[int] = mapped_column(BigInteger)
    delta_qty: Mapped[int] = mapped_column(BigInteger)
    from_weight_pct: Mapped[Decimal | None] = mapped_column(Pct)
    to_weight_pct: Mapped[Decimal | None] = mapped_column(Pct)
    delta_value: Mapped[Decimal | None] = mapped_column(Money)
    activity: Mapped[str] = mapped_column(String(8), index=True)
    confidence: Mapped[str] = mapped_column(String(16), default="INFERRED")
    # v2 (engine/positions): position value on each side, weight change, relative quantity change.
    from_value: Mapped[Decimal | None] = mapped_column(Money)
    to_value: Mapped[Decimal | None] = mapped_column(Money)
    delta_weight_pct: Mapped[Decimal | None] = mapped_column(Pct)
    # Wider than Pct: a 1-share odd lot that becomes a real position is a ten-digit percentage, and Postgres
    # aborts the whole rebuild on a numeric overflow.
    pct_change_qty: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    # Reporting periods the diff spans (TR month-ends, US quarters): 1 = consecutive, 2 = one report was missed.
    gap_periods: Mapped[int] = mapped_column(Integer, default=1)


class MarketPrice(Base):
    __tablename__ = "market_prices"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(Money)
    high: Mapped[Decimal | None] = mapped_column(Money)
    low: Mapped[Decimal | None] = mapped_column(Money)
    close: Mapped[Decimal] = mapped_column(Money)
    volume: Mapped[int | None] = mapped_column(BigInteger)
    source: Mapped[str] = mapped_column(String(16), nullable=False, server_default="yahoo")  # provider name (ingestion/prices/provider.PROVIDERS) or "csv"


# --------------------------------------------------------------------------- fundamentals (reported figures)


class Fundamental(Base):
    """One reported financial statement (income / balance / cashflow) of one instrument for one period, as the
    provider printed it. `items` holds the canonical keys of its kind (ingestion/fundamentals/provider.CANONICAL_KEYS);
    a line the filing did not carry is null, never computed. `currency` is the reporting currency; `source` and
    `fetched_at` are the provenance of every number in the row."""

    __tablename__ = "fundamentals"
    __table_args__ = (UniqueConstraint("instrument_id", "kind", "period_kind", "period_end"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    kind: Mapped[str] = mapped_column(String(8))  # income / balance / cashflow
    period_kind: Mapped[str] = mapped_column(String(9))  # annual / quarterly
    period_end: Mapped[date] = mapped_column(Date)
    currency: Mapped[str | None] = mapped_column(String(3))
    items: Mapped[dict] = mapped_column(JSON)
    source: Mapped[str] = mapped_column(String(16))  # provider name (ingestion/fundamentals/provider.PROVIDERS)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


class FundamentalSnapshot(Base):
    """Trailing valuation / profitability metrics of one instrument as the provider stated them on `as_of` (the
    fetch date). `metrics` holds the contract's snapshot keys (provider.SNAPSHOT_KEYS), percentages already ×100;
    market cap, EV, the 52-week range and EPS are in `quote_currency` (the listing currency), the TTM revenue /
    EBITDA / net income in `currency` (the reporting currency, as the statements); what the provider did not state
    is null."""

    __tablename__ = "fundamental_snapshots"
    __table_args__ = (UniqueConstraint("instrument_id", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    as_of: Mapped[date] = mapped_column(Date)
    metrics: Mapped[dict] = mapped_column(JSON)
    currency: Mapped[str | None] = mapped_column(String(3))
    quote_currency: Mapped[str | None] = mapped_column(String(3))
    source: Mapped[str] = mapped_column(String(16))
    fetched_at: Mapped[datetime] = mapped_column(DateTime)


# --------------------------------------------------------------------------- intelligence layer


class Signal(Base):
    __tablename__ = "signals"
    __table_args__ = (UniqueConstraint("instrument_id", "fund_id", "signal_type", "window_end"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market_code: Mapped[str] = mapped_column(ForeignKey("markets.code"))
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    fund_id: Mapped[int | None] = mapped_column(ForeignKey("funds.id"))
    signal_type: Mapped[str] = mapped_column(String(32), index=True)
    strength: Mapped[int] = mapped_column(Integer)  # 0..100
    window_start: Mapped[date] = mapped_column(Date)
    window_end: Mapped[date] = mapped_column(Date, index=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    evidence: Mapped[dict] = mapped_column(JSON)  # the "why" — shown to the user verbatim
    confidence: Mapped[str] = mapped_column(String(16))


class Score(Base):
    __tablename__ = "scores"
    __table_args__ = (UniqueConstraint("instrument_id", "fund_id", "score_type", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), index=True)
    fund_id: Mapped[int | None] = mapped_column(ForeignKey("funds.id"))
    score_type: Mapped[str] = mapped_column(String(16), index=True)
    as_of: Mapped[date] = mapped_column(Date, index=True)
    raw_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    adjusted_score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    components: Mapped[dict] = mapped_column(JSON)  # powers the "Why 87?" button


class SignalOutcome(Base):
    """Forward returns after a signal fired. Filled in later; the credibility layer."""

    __tablename__ = "signal_outcomes"

    signal_id: Mapped[int] = mapped_column(ForeignKey("signals.id"), primary_key=True)
    price_at_signal: Mapped[Decimal] = mapped_column(Money)
    ret_7d: Mapped[Decimal | None] = mapped_column(Pct)
    ret_30d: Mapped[Decimal | None] = mapped_column(Pct)
    ret_90d: Mapped[Decimal | None] = mapped_column(Pct)
    max_return: Mapped[Decimal | None] = mapped_column(Pct)
    max_drawdown: Mapped[Decimal | None] = mapped_column(Pct)
    computed_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


# --------------------------------------------------------------------------- user layer


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(256))
    name: Mapped[str] = mapped_column(String(128))
    plan: Mapped[str] = mapped_column(String(16), default="FREE")  # FREE / PRO / PRO_PLUS
    role: Mapped[str] = mapped_column(String(16), default="USER")  # USER / ADMIN
    # Delivery preferences for alerts and the morning brief.
    notify_email: Mapped[bool] = mapped_column(Boolean, default=False)
    notify_telegram_chat_id: Mapped[str | None] = mapped_column(String(32))
    notify_brief: Mapped[bool] = mapped_column(Boolean, default=True)
    lang: Mapped[str] = mapped_column(String(2), default="tr")  # UI + AI note + delivery language: "tr" / "en"
    brief_markets: Mapped[list] = mapped_column(JSON, default=lambda: ["TR"])  # which morning briefs to deliver: subset of ["TR", "US"]
    # Bumped on password change / role change / "log out everywhere": tokens carry it and older ones die.
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False)  # set by the e-mailed verification link; login is not blocked on it
    totp_secret: Mapped[str | None] = mapped_column(String(64))  # base32 TOTP secret once MFA is enabled (admin accounts)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)


class AuthToken(Base):
    """Single-use e-mail tokens (password reset, e-mail verification). Only the SHA-256 of the token is stored,
    so a database leak does not hand out working links; `used_at` makes each link one-shot."""

    __tablename__ = "auth_tokens"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), index=True)  # domain.enums.AuthTokenKind
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    used_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class RateHit(Base):
    """One counted request for a rate-limit / lockout key. Shared across uvicorn workers via the database;
    rows older than the longest window are swept on the fly (see api/hardening)."""

    __tablename__ = "rate_hits"
    __table_args__ = (Index("ix_rate_hits_key_ts", "key", "ts"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(160))
    ts: Mapped[datetime] = mapped_column(DateTime)


class LiveEvent(Base):
    """One row per thing an open tab should react to (see services/live). Any process — scheduler, admin worker
    thread, either API worker — appends; every /events/stream tails the table, so fan-out needs no broker.
    `owner_id` set = private to that user (a fired alert); `market_code` set = only tabs on that market care."""

    __tablename__ = "live_events"
    __table_args__ = (Index("ix_live_events_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(24))  # services.live.KINDS
    market_code: Mapped[str | None] = mapped_column(String(2))
    owner_id: Mapped[str | None] = mapped_column(String(64))
    payload: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class PipelineRun(Base):
    """One admin-triggered pipeline run. `lock_key` is PIPELINE_LOCK while it is in progress and NULL afterwards; the
    unique constraint is the cross-process lock, so two admins (or two workers) cannot start two runs at once."""

    __tablename__ = "pipeline_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    lock_key: Mapped[str | None] = mapped_column(String(16), unique=True)
    status: Mapped[str] = mapped_column(String(16), default=PipelineStatus.RUNNING.value, index=True)  # domain.enums.PipelineStatus
    started_by: Mapped[str | None] = mapped_column(String(254))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    result: Mapped[dict | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class BriefDelivery(Base):
    """Record of one morning brief handed to one user, so re-running the 08:30 job never re-sends."""

    __tablename__ = "brief_deliveries"
    __table_args__ = (UniqueConstraint("user_id", "market", "day", "lang", "channel"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    market: Mapped[str] = mapped_column(String(8))
    day: Mapped[date] = mapped_column(Date)
    lang: Mapped[str] = mapped_column(String(2))
    channel: Mapped[str] = mapped_column(String(16))  # domain.enums.DeliveryChannel
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))



class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    name: Mapped[str] = mapped_column(String(64))


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id"), index=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    fund_id: Mapped[int | None] = mapped_column(ForeignKey("funds.id"))
    institution_id: Mapped[int | None] = mapped_column(ForeignKey("institutions.id"))


class AlertRule(Base):
    __tablename__ = "alert_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    instrument_id: Mapped[int | None] = mapped_column(ForeignKey("instruments.id"))
    fund_id: Mapped[int | None] = mapped_column(ForeignKey("funds.id"))
    rule_type: Mapped[str] = mapped_column(String(32))  # NEW_FUND_POSITION, FUND_EXIT, SCORE_ABOVE...
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (UniqueConstraint("owner_id", "dedup_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    alert_rule_id: Mapped[int | None] = mapped_column(ForeignKey("alert_rules.id"))
    dedup_key: Mapped[str] = mapped_column(String(160))  # rule × subject × period → fires once
    title: Mapped[str] = mapped_column(String(256))
    body: Mapped[str] = mapped_column(Text)
    link: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    read_at: Mapped[datetime | None] = mapped_column(DateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime)  # sent via email/telegram


# --------------------------------------------------------------------------- news (headlines only)


class NewsItem(Base):
    """Headline + link from an RSS/News API source. Never the article body — that stays with the publisher."""

    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("url"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    market_code: Mapped[str] = mapped_column(ForeignKey("markets.code"), index=True)
    source: Mapped[str] = mapped_column(String(64), index=True)
    title: Mapped[str] = mapped_column(String(512))
    url: Mapped[str] = mapped_column(String(1024))
    published_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    symbols: Mapped[list] = mapped_column(JSON, default=list)  # matched instrument symbols
    tags: Mapped[list] = mapped_column(JSON, default=list)  # names of the news rules that matched
    ai: Mapped[dict | None] = mapped_column(JSON)  # {"summary_tr", "symbols", "sentiment", "sector"} from the AI pass
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class NewsRule(Base):
    """Newsomatic-style import rule, ported: keywords / exclusions / source filters / language / age → tags & symbols."""

    __tablename__ = "news_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64))
    market_code: Mapped[str] = mapped_column(ForeignKey("markets.code"), default="TR")
    query: Mapped[str] = mapped_column(String(512), default="")  # "banka, faiz, halka arz"  (any of; comma-separated; supports "a+b" for all-of)
    exclusion: Mapped[str] = mapped_column(String(512), default="")  # comma-separated words that reject the headline
    only_sources: Mapped[str] = mapped_column(String(512), default="")  # comma-separated source names; empty = all
    remove_sources: Mapped[str] = mapped_column(String(512), default="")
    language: Mapped[str] = mapped_column(String(8), default="")  # "tr" / "en" / "" (NewsAPI queries only)
    max_age_days: Mapped[int] = mapped_column(Integer, default=3)
    symbols: Mapped[list] = mapped_column(JSON, default=list)  # instruments to tag matched headlines with
    newsapi_query: Mapped[str] = mapped_column(String(256), default="")  # extra NewsAPI "everything" query
    ai_summary: Mapped[bool] = mapped_column(Boolean, default=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


# --------------------------------------------------------------------------- AI notes (cached model output)


class Release(Base):
    """A desktop app version. DRAFT while files are being uploaded, PUBLISHED when the updater may serve it,
    WITHDRAWN if pulled. Version numbers are handed out by the server (`next_version`) so Mac and Linux/Windows
    builds made on different machines land in the same release."""

    __tablename__ = "releases"

    id: Mapped[int] = mapped_column(primary_key=True)
    version: Mapped[str] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="DRAFT", index=True)  # DRAFT / PUBLISHED / WITHDRAWN
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str | None] = mapped_column(String(254))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    published_at: Mapped[datetime | None] = mapped_column(DateTime)

    files: Mapped[list[ReleaseFile]] = relationship(back_populates="release", cascade="all, delete-orphan")


class ReleaseFile(Base):
    """One artifact of a release. `platform` is the Tauri target key (darwin-aarch64, darwin-x86_64,
    windows-x86_64, linux-x86_64); `kind` INSTALLER (dmg/exe/deb/AppImage for humans) or UPDATE (the
    updater bundle: .app.tar.gz / -setup.exe / .AppImage with its minisign signature)."""

    __tablename__ = "release_files"
    __table_args__ = (UniqueConstraint("release_id", "filename"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    release_id: Mapped[int] = mapped_column(ForeignKey("releases.id"), index=True)
    platform: Mapped[str] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16))  # INSTALLER / UPDATE
    filename: Mapped[str] = mapped_column(String(256))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    signature: Mapped[str | None] = mapped_column(Text)  # minisign signature (UPDATE files)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    downloads: Mapped[int] = mapped_column(Integer, default=0)

    release: Mapped[Release] = relationship(back_populates="files")


class AppSetting(Base):
    """Runtime override of a non-secret setting, edited from the admin UI. Secrets never live here."""

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[dict] = mapped_column(JSON)  # {"v": <json value>} so scalars round-trip through the JSON column
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_by: Mapped[str | None] = mapped_column(String(254))


class AuditEvent(Base):
    """Security-relevant events (login ok/fail, password change, role/plan change, lockouts). No secrets, no PII
    beyond the account e-mail; IP is the resolved client address. Append-only."""

    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(48), index=True)  # auth.login_ok, auth.login_fail, auth.locked, auth.password_changed, admin.user_patch, ...
    actor: Mapped[str | None] = mapped_column(String(254))  # e-mail of the acting account (or attempted e-mail)
    subject: Mapped[str | None] = mapped_column(String(254))  # affected account / object
    ip: Mapped[str | None] = mapped_column(String(64))
    detail: Mapped[str | None] = mapped_column(String(512))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC), index=True)


class WaitlistEntry(Base):
    """Early-access signup from the public landing page (instilens.com)."""

    __tablename__ = "waitlist"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    name: Mapped[str | None] = mapped_column(String(128))
    lang: Mapped[str] = mapped_column(String(2), default="tr")
    source: Mapped[str | None] = mapped_column(String(64))  # utm / referrer, free text
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class AiNote(Base):
    """Cached AI-written text: per-stock assessment, daily brief. Always descriptive, always with the
    data it was written from (`data`) so the UI can show provenance. One per (kind, market, subject, day)."""

    __tablename__ = "ai_notes"
    __table_args__ = (UniqueConstraint("kind", "market_code", "subject", "as_of", "lang", name="uq_ai_notes_note"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # STOCK_ASSESSMENT / DAILY_BRIEF
    market_code: Mapped[str] = mapped_column(ForeignKey("markets.code"))
    subject: Mapped[str] = mapped_column(String(32))  # symbol, or "market" for the brief
    as_of: Mapped[date] = mapped_column(Date, index=True)
    lang: Mapped[str] = mapped_column(String(2), default="tr")  # language the note was written in
    content: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class PushSubscription(Base):
    """Web Push (VAPID) subscription of a user's browser/phone. One row per device."""

    __tablename__ = "push_subscriptions"
    __table_args__ = (UniqueConstraint("endpoint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    owner_id: Mapped[str] = mapped_column(String(64), index=True)
    endpoint: Mapped[str] = mapped_column(String(1024))
    p256dh: Mapped[str] = mapped_column(String(256))
    auth: Mapped[str] = mapped_column(String(128))
    user_agent: Mapped[str | None] = mapped_column(String(256))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_ok_at: Mapped[datetime | None] = mapped_column(DateTime)
