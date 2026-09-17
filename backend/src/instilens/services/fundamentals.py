"""Fundamentals: reported financial statements and trailing metrics per instrument (Faz 3).

`refresh` pulls from whichever provider `resolve_provider()` picks and upserts `fundamentals` /
`fundamental_snapshots` rows — provider-agnostic on purpose, the CLI, scheduler and API never see a vendor module.
`stock_fundamentals` and `summary` are the read models behind /stocks/{symbol}/fundamentals and stock_detail.
`derived` is the only arithmetic here — margins, leverage and year-over-year growth from the newest statement — and
is a pure function, so every ratio on the page is reproducible from the stored rows. Descriptive figures only:
nothing in this module rates, targets or recommends.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from sqlalchemy import func, or_, select, union_all
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.enums import Market
from instilens.domain.models import (
    Fundamental,
    FundamentalSnapshot,
    Instrument,
    PositionChange,
    TransactionEvent,
    WatchlistItem,
)
from instilens.ingestion.fundamentals.provider import (
    PERIOD_KINDS,
    SNAPSHOT_KEYS,
    STATEMENT_KINDS,
    MetricsSnapshot,
    PeriodKind,
    ProviderUnavailable,
    Statement,
    resolve_provider,
)
from instilens.ingestion.prices.yahoo import yahoo_symbol
from instilens.services.entities import MARKETS

log = logging.getLogger("instilens.fundamentals")

UNIVERSE_DAYS = 400  # a position change or transaction this recent keeps an instrument in the refresh universe
MAX_STATEMENTS = 8  # per kind in the API payload: eight years or eight quarters
YOY_TOLERANCE_DAYS = 45  # fiscal calendars drift (52/53-week years): the year-earlier statement within this window counts
DERIVED_KEYS = ("gross_margin", "operating_margin", "net_margin", "fcf_margin", "debt_to_equity", "revenue_growth_yoy", "net_income_growth_yoy")


# --------------------------------------------------------------------------- refresh (write path)


def universe(session: Session, market: str, limit: int | None = None) -> list[Instrument]:
    """Instruments of `market` worth asking a provider about: seen in a position change or a disclosed transaction
    within the last UNIVERSE_DAYS, or on any watchlist. Never the whole US instrument table — most of it is CUSIP
    placeholders the resolver created for unmapped 13F rows, which no provider can be asked for (yahoo_symbol → None).
    Stalest first — never fetched, then by the oldest `fetched_at` of its rows, then by symbol — and at most `limit`
    of them: a capped weekly run walks the whole universe over a few weeks instead of re-fetching the same head
    (and, after a rate-limit break, restarting from "A")."""
    since = date.today() - timedelta(days=UNIVERSE_DAYS)
    in_changes = select(PositionChange.instrument_id).where(PositionChange.period_end >= since)
    in_events = select(TransactionEvent.instrument_id).where(TransactionEvent.effective_date >= since)
    watched = select(WatchlistItem.instrument_id).where(WatchlistItem.instrument_id.is_not(None))
    rows = union_all(
        select(Fundamental.instrument_id.label("instrument_id"), Fundamental.fetched_at.label("fetched_at")),
        select(FundamentalSnapshot.instrument_id.label("instrument_id"), FundamentalSnapshot.fetched_at.label("fetched_at")),
    ).subquery()
    last = select(rows.c.instrument_id, func.max(rows.c.fetched_at).label("fetched_at")).group_by(rows.c.instrument_id).subquery()
    candidates = session.scalars(
        select(Instrument)
        .outerjoin(last, last.c.instrument_id == Instrument.id)
        .where(Instrument.market_code == market, or_(Instrument.id.in_(in_changes), Instrument.id.in_(in_events), Instrument.id.in_(watched)))
        .order_by(last.c.fetched_at.asc().nulls_first(), Instrument.symbol)
    )
    out: list[Instrument] = []
    for inst in candidates:
        if yahoo_symbol(market, inst.symbol) is None:
            continue
        out.append(inst)
        if limit is not None and len(out) >= limit:
            break
    return out


def refresh(session: Session, market: str, symbols: Sequence[str] | None = None, *, period_kinds: Sequence[PeriodKind] = PERIOD_KINDS, pause_s: float = 0.4) -> int:
    """Upsert the statements (`period_kinds`) and the metrics snapshot of the stalest `fundamentals_max_instruments`
    instruments of `universe(market)` (or of the given `symbols`, uncapped); each row records the provider in `source`
    and the fetch time in `fetched_at`. Returns the number of rows written. One ticker's failure is logged and
    skipped — a bad symbol never stops the batch; a provider outage (ProviderUnavailable) is logged and ends the run
    early, what was written stays. A period the provider has no statements for ends that instrument's statement
    pulls (an ETF has no quarterly ones either). `pause_s` sleeps before every provider call but the first: each
    call is several HTTP requests, and a few hundred symbols in a burst look like a scrape."""
    provider = resolve_provider()
    if symbols:
        wanted = [s.strip().upper() for s in symbols if s.strip()]
        instruments = session.scalars(select(Instrument).where(Instrument.market_code == market, Instrument.symbol.in_(wanted)).order_by(Instrument.symbol)).all()
        if missing := sorted(set(wanted) - {i.symbol for i in instruments}):
            log.warning("%s fundamentals: unknown symbols skipped: %s", market, ", ".join(missing))
    else:
        instruments = universe(session, market, limit=settings.fundamentals_max_instruments)
    written, answered, calls = 0, 0, 0

    def pace() -> None:
        nonlocal calls
        if calls and pause_s:
            time.sleep(pause_s)
        calls += 1

    for inst in instruments:
        try:
            fetched: list[tuple[PeriodKind, Statement]] = []
            for period in period_kinds:
                pace()
                statements = provider.statements(market, inst.symbol, period)
                if not statements:
                    break
                fetched.extend((period, st) for st in statements)
            pace()
            snap = provider.snapshot(market, inst.symbol)
        except ProviderUnavailable as exc:
            log.warning("%s fundamentals: %s unavailable, stopping after %s rows: %s", market, provider.name, written, exc)
            break
        except Exception as exc:  # noqa: BLE001 — one bad ticker never stops the batch
            log.warning("%s fundamentals: %s skipped: %s: %s", market, inst.symbol, type(exc).__name__, str(exc)[:200])
            continue
        if not fetched and snap is None:
            log.info("%s fundamentals: %s has nothing at %s", market, inst.symbol, provider.name)
            continue
        assumed = {st.currency for _, st in fetched if st.currency_assumed}
        if snap is not None:
            assumed |= ({snap.currency} if snap.currency_assumed else set()) | ({snap.quote_currency} if snap.quote_currency_assumed else set())
        if assumed:
            log.warning("%s fundamentals: %s states no currency; %s assumed", market, inst.symbol, ", ".join(sorted(c or "?" for c in assumed)))
        written += _store(session, inst, fetched, snap, provider.name, datetime.now(UTC))
        answered += 1
        session.flush()
    if instruments and not answered:
        log.warning("%s fundamentals: %s answered for none of %s instruments", market, provider.name, len(instruments))
    return written


def _store(session: Session, inst: Instrument, fetched: list[tuple[PeriodKind, Statement]], snap: MetricsSnapshot | None, source: str, now: datetime) -> int:
    """Upsert one instrument's rows: statements keyed by (kind, period_kind, period_end), the snapshot by as_of.
    A re-run replaces the row's numbers in place — the provider's latest print of a period is the row."""
    n = 0
    if fetched:
        existing = {(r.kind, r.period_kind, r.period_end): r for r in session.scalars(select(Fundamental).where(Fundamental.instrument_id == inst.id))}
        for period_kind, st in fetched:
            row = existing.get((st.kind, period_kind, st.period_end))
            if row is None:
                row = Fundamental(instrument_id=inst.id, kind=st.kind, period_kind=period_kind, period_end=st.period_end, items={}, source=source, fetched_at=now)
                session.add(row)
                existing[(st.kind, period_kind, st.period_end)] = row
            row.items, row.currency, row.source, row.fetched_at = dict(st.items), st.currency, source, now
            n += 1
    if snap is not None:
        row = session.scalar(select(FundamentalSnapshot).where(FundamentalSnapshot.instrument_id == inst.id, FundamentalSnapshot.as_of == snap.as_of))
        if row is None:
            row = FundamentalSnapshot(instrument_id=inst.id, as_of=snap.as_of, metrics={}, source=source, fetched_at=now)
            session.add(row)
        row.metrics = {k: getattr(snap, k) for k in SNAPSHOT_KEYS}
        row.currency, row.quote_currency, row.source, row.fetched_at = snap.currency, snap.quote_currency, source, now
        if snap.shares_outstanding is not None:
            inst.shares_outstanding, inst.shares_as_of = int(snap.shares_outstanding), snap.as_of
        n += 1
    return n


# --------------------------------------------------------------------------- derived ratios (pure)


class StatementLike(Protocol):
    """What `derived` reads: a provider Statement or a stored Fundamental row."""

    kind: str
    period_end: date
    items: dict


def _pct(numerator: float | None, denominator: float | None) -> float | None:
    """numerator / denominator as a percentage; None when either is missing or the denominator is 0."""
    if numerator is None or denominator is None or denominator == 0:
        return None
    return round(numerator / denominator * 100.0, 2)


def _growth(new: float | None, old: float | None) -> float | None:
    """Change from `old` to `new` as a percentage of |old|, so a loss that shrinks reads as positive growth;
    None when either is missing or the base is 0."""
    if new is None or old is None or old == 0:
        return None
    return round((new - old) / abs(old) * 100.0, 2)


def _year_earlier(statements: Sequence[StatementLike], newest: StatementLike) -> StatementLike | None:
    """The statement whose period ended one year before `newest` (within YOY_TOLERANCE_DAYS): the previous fiscal
    year for annual rows, four quarters back for quarterly ones — never the previous quarter."""
    end = newest.period_end
    try:
        target = end.replace(year=end.year - 1)
    except ValueError:  # 29 February
        target = end.replace(year=end.year - 1, day=28)
    candidates = [s for s in statements if s is not newest and abs((s.period_end - target).days) <= YOY_TOLERANCE_DAYS]
    return min(candidates, key=lambda s: abs((s.period_end - target).days)) if candidates else None


def derived(statements: Sequence[StatementLike]) -> dict:
    """The contract's `derived` block from the statements of ONE period kind (annual or quarterly), any order.
    Anchored on the newest income statement (`period_end`): margins divide its lines by its revenue, fcf_margin
    uses the cashflow statement of the same period, debt_to_equity the balance sheet of the same period (else the
    newest), growth compares it with the statement one year earlier. Ratios are percentages (12.3 = 12.3 %);
    anything with a missing or zero denominator is None."""
    by_kind = {k: sorted((s for s in statements if s.kind == k), key=lambda s: s.period_end, reverse=True) for k in STATEMENT_KINDS}
    out: dict[str, float | str | None] = dict.fromkeys((*DERIVED_KEYS, "period_end"))
    income, balances, cashflows = by_kind["income"], by_kind["balance"], by_kind["cashflow"]
    newest = income[0] if income else None
    anchor = newest.period_end if newest else max((s.period_end for k in by_kind for s in by_kind[k]), default=None)
    if anchor is None:
        return out
    out["period_end"] = anchor.isoformat()
    balance = next((b for b in balances if b.period_end == anchor), balances[0] if balances else None)
    if balance is not None:
        out["debt_to_equity"] = _pct(balance.items.get("total_debt"), balance.items.get("equity"))
    if newest is None:
        return out
    revenue = newest.items.get("revenue")
    out["gross_margin"] = _pct(newest.items.get("gross_profit"), revenue)
    out["operating_margin"] = _pct(newest.items.get("operating_income"), revenue)
    out["net_margin"] = _pct(newest.items.get("net_income"), revenue)
    cashflow = next((c for c in cashflows if c.period_end == anchor), None)
    out["fcf_margin"] = _pct(cashflow.items.get("free_cf") if cashflow else None, revenue)
    prior = _year_earlier(income, newest)
    out["revenue_growth_yoy"] = _growth(revenue, prior.items.get("revenue") if prior else None)
    out["net_income_growth_yoy"] = _growth(newest.items.get("net_income"), prior.items.get("net_income") if prior else None)
    return out


# --------------------------------------------------------------------------- read models


def _latest_snapshot(session: Session, instrument_id: int) -> FundamentalSnapshot | None:
    return session.scalar(
        select(FundamentalSnapshot).where(FundamentalSnapshot.instrument_id == instrument_id).order_by(FundamentalSnapshot.as_of.desc(), FundamentalSnapshot.id.desc()).limit(1)
    )


def _statement_rows(session: Session, instrument_id: int, period: str) -> list[Fundamental]:
    return session.scalars(
        select(Fundamental).where(Fundamental.instrument_id == instrument_id, Fundamental.period_kind == period).order_by(Fundamental.period_end.desc(), Fundamental.id)
    ).all()


def _currency(market: str, rows: Sequence[Fundamental], snap: FundamentalSnapshot | None) -> str:
    """The reporting currency of the newest statement, else the snapshot's, else the market's trading currency."""
    return next((r.currency for r in rows if r.currency), None) or (snap.currency if snap else None) or MARKETS[Market(market)].currency


def _snapshot_json(snap: FundamentalSnapshot) -> dict:
    """The snapshot block: its metrics plus `quote_currency`, the listing currency market cap, EV, the 52-week range
    and EPS are in (the payload's `currency` is the reporting one the statements and the TTM lines use)."""
    return {"as_of": snap.as_of.isoformat(), "quote_currency": snap.quote_currency, **{k: snap.metrics.get(k) for k in SNAPSHOT_KEYS}}


def stock_fundamentals(session: Session, market: str, symbol: str, period: PeriodKind = "annual") -> dict | None:
    """The /stocks/{symbol}/fundamentals payload: the latest metrics snapshot, up to MAX_STATEMENTS statements per
    kind for `period` (newest first) and the ratios derived from them. None for an unknown symbol; a known symbol
    nothing has been fetched for answers with an empty state (snapshot null, empty statements, null ratios) —
    never a placeholder number."""
    inst = session.scalar(select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol.upper()))
    if inst is None:
        return None
    rows = _statement_rows(session, inst.id, period)
    snap = _latest_snapshot(session, inst.id)
    statements = {kind: [r for r in rows if r.kind == kind][:MAX_STATEMENTS] for kind in STATEMENT_KINDS}
    fetched = [r.fetched_at for r in rows] + ([snap.fetched_at] if snap else [])
    return {
        "symbol": inst.symbol,
        "name": inst.name,
        "market": inst.market_code,
        "currency": _currency(market, rows, snap),
        "source": rows[0].source if rows else snap.source if snap else resolve_provider().name,
        "fetched_at": max(fetched).isoformat() if fetched else None,
        "snapshot": _snapshot_json(snap) if snap else None,
        "period": period,
        "statements": {kind: [{"period_end": r.period_end.isoformat(), "items": r.items} for r in lst] for kind, lst in statements.items()},
        "derived": derived([r for lst in statements.values() for r in lst]),
    }


def summary(session: Session, instrument_id: int) -> dict | None:
    """The small `fundamentals` block of stock_detail: valuation from the latest snapshot (market cap in
    `quote_currency`), net margin and revenue growth from the newest annual statements (in `currency`). None until
    something has been fetched for the instrument."""
    inst = session.get(Instrument, instrument_id)
    if inst is None:
        return None
    snap = _latest_snapshot(session, instrument_id)
    annual = _statement_rows(session, instrument_id, "annual")
    if snap is None and not annual:
        return None
    ratios = derived(annual)
    metrics = snap.metrics if snap else {}
    return {
        "as_of": (snap.as_of if snap else annual[0].period_end).isoformat(),
        "market_cap": metrics.get("market_cap"),
        "pe": metrics.get("pe"),
        "price_to_book": metrics.get("price_to_book"),
        "net_margin": ratios["net_margin"],
        "revenue_growth_yoy": ratios["revenue_growth_yoy"],
        "dividend_yield": metrics.get("dividend_yield"),
        "shares_outstanding": inst.shares_outstanding if inst.shares_outstanding is not None else metrics.get("shares_outstanding"),
        "currency": _currency(inst.market_code, annual, snap),
        "quote_currency": snap.quote_currency if snap else None,
        "source": snap.source if snap else annual[0].source,
    }
