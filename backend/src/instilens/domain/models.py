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


class MarketPrice(Base):
    __tablename__ = "market_prices"

    instrument_id: Mapped[int] = mapped_column(ForeignKey("instruments.id"), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    close: Mapped[Decimal] = mapped_column(Money)
    volume: Mapped[int | None] = mapped_column(BigInteger)


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
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime)



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


class AiNote(Base):
    """Cached AI-written text: per-stock assessment, daily brief. Always descriptive, always with the
    data it was written from (`data`) so the UI can show provenance. One per (kind, market, subject, day)."""

    __tablename__ = "ai_notes"
    __table_args__ = (UniqueConstraint("kind", "market_code", "subject", "as_of"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), index=True)  # STOCK_ASSESSMENT / DAILY_BRIEF
    market_code: Mapped[str] = mapped_column(ForeignKey("markets.code"))
    subject: Mapped[str] = mapped_column(String(32))  # symbol, or "market" for the brief
    as_of: Mapped[date] = mapped_column(Date, index=True)
    content: Mapped[str] = mapped_column(Text)
    data: Mapped[dict] = mapped_column(JSON, default=dict)
    model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
