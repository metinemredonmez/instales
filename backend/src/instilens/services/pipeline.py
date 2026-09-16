"""The end-to-end chain that defines "MVP done":

    disclosure arrives → stored raw → parsed → entities resolved → normalized facts written with
    confidence → positions rebuilt → signals & scores recomputed.

Each stage is idempotent and re-runnable; re-running never double-counts.
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
from collections import defaultdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from instilens.domain.enums import (
    ActivityType,
    Confidence,
    DisclosureKind,
    Market,
    ParseStatus,
    ScoreType,
    Source,
)
from instilens.domain.models import (
    Disclosure,
    Fund,
    Instrument,
    MarketPrice,
    PortfolioSnapshot,
    PositionChange,
    Score,
    Signal,
    SnapshotHolding,
    TransactionEvent,
    TransactionEventFund,
)
from instilens.domain.schemas import RawDisclosure
from instilens.engine import scoring
from instilens.engine.positions import HoldingView, SnapshotView, diff_snapshots
from instilens.engine.signals import (
    FundMove,
    PeriodFlow,
    detect_accumulation,
    detect_cluster,
    detect_divergence,
)
from instilens.ingestion.base import SourceAdapter
from instilens.parsing import parse_portfolio_report, parse_share_transaction
from instilens.parsing.sec_13f import parse_13f
from instilens.services.entities import EntityResolver

log = logging.getLogger(__name__)


# --------------------------------------------------------------------------- stage 1: ingest


def ingest(session: Session, adapter: SourceAdapter) -> int:
    """Pull new disclosures from an adapter and store them raw. Returns number stored."""
    resolver = EntityResolver(session)
    resolver.ensure_markets()
    stored = 0
    if hasattr(adapter, "known"):  # polite adapters skip disclosures we already hold
        adapter.known |= set(session.scalars(select(Disclosure.source_id)))
    since = _last_source_id(session, raw_source=None)
    source = adapter.iter_fetch(since) if hasattr(adapter, "iter_fetch") else adapter.fetch(since_source_id=since)
    for raw in source:
        if _store_raw(session, raw) is not None:
            stored += 1
            session.commit()  # each disclosure is durable as soon as it is parsed — long polite runs may be interrupted
    session.flush()
    return stored


def _last_source_id(session: Session, raw_source: Source | None) -> str | None:
    stmt = select(Disclosure.source_id)
    if raw_source:
        stmt = stmt.where(Disclosure.source == raw_source)
    ids = [s for s in session.scalars(stmt) if s.isdigit()]
    return str(max(int(s) for s in ids)) if ids else None


def _store_raw(session: Session, raw: RawDisclosure) -> Disclosure | None:
    exists = session.scalar(
        select(Disclosure).where(Disclosure.source == raw.source, Disclosure.source_id == raw.source_id)
    )
    if exists:
        return None
    payload_json = json.dumps(raw.payload, sort_keys=True, default=str)
    row = Disclosure(
        market_code=raw.market,
        source=raw.source,
        source_id=raw.source_id,
        kind=raw.kind,
        published_at=raw.published_at,
        raw_uri=raw.raw_uri,
        raw_hash=hashlib.sha256(payload_json.encode()).hexdigest(),
        payload=raw.payload,
        parse_status=ParseStatus.PENDING,
    )
    session.add(row)
    session.flush()

    amends = raw.payload.get("amends_source_id")
    if amends:
        old = session.scalar(
            select(Disclosure).where(Disclosure.source == raw.source, Disclosure.source_id == str(amends))
        )
        if old is not None:
            row.supersedes_id = old.id
            old.is_superseded = True
            for ev in session.scalars(select(TransactionEvent).where(TransactionEvent.disclosure_id == old.id)):
                ev.is_superseded = True
    return row


# --------------------------------------------------------------------------- stage 2: parse


def parse_pending(session: Session) -> int:
    resolver = EntityResolver(session)
    resolver.ensure_markets()
    parsed = 0
    pending = session.scalars(
        select(Disclosure).where(Disclosure.parse_status == ParseStatus.PENDING).order_by(Disclosure.published_at)
    ).all()
    for disc in pending:
        raw = RawDisclosure(
            market=Market(disc.market_code),
            source=Source(disc.source),
            source_id=disc.source_id,
            kind=DisclosureKind(disc.kind),
            published_at=disc.published_at,
            raw_uri=disc.raw_uri,
            payload=disc.payload,
        )
        try:
            if raw.kind is DisclosureKind.KAP_SHARE_TRANSACTION:
                _write_transaction_event(session, resolver, disc, raw)
            elif raw.kind is DisclosureKind.KAP_PORTFOLIO_REPORT:
                _write_snapshot(session, resolver, disc, parse_portfolio_report(raw))
            elif raw.kind is DisclosureKind.SEC_13F:
                _write_snapshot(session, resolver, disc, parse_13f(raw))
            else:
                disc.parse_status = ParseStatus.UNSUPPORTED
                continue
            disc.parse_status = ParseStatus.PARSED
            disc.parsed_at = datetime.now(UTC)
            parsed += 1
        except Exception as exc:  # keep the pipeline moving; failures are visible in the table
            log.exception("parse failed for %s/%s", disc.source, disc.source_id)
            disc.parse_status = ParseStatus.FAILED
            disc.parse_error = f"{type(exc).__name__}: {exc}"
    session.flush()
    return parsed


def _write_transaction_event(session: Session, resolver: EntityResolver, disc: Disclosure, raw: RawDisclosure) -> None:
    ev = parse_share_transaction(raw)
    institution = resolver.institution(ev.market, ev.institution_ref, ev.institution_name)
    instrument = resolver.instrument(ev.market, ev.instrument_symbol, raw.payload.get("subject_name"))
    disc.institution_id = institution.id
    row = TransactionEvent(
        disclosure_id=disc.id,
        market_code=ev.market,
        instrument_id=instrument.id,
        institution_id=institution.id,
        side=ev.side,
        buy_nominal=ev.buy_nominal,
        sell_nominal=ev.sell_nominal,
        net_nominal=ev.net_nominal,
        avg_price=ev.avg_price,
        net_value=ev.net_value,
        effective_date=ev.effective_date,
        published_at=ev.published_at,
        ownership_before_pct=ev.ownership_before_pct,
        ownership_after_pct=ev.ownership_after_pct,
        confidence=ev.confidence,
        is_superseded=disc.is_superseded,
    )
    for code, allocated in ev.fund_allocations.items():
        fund = resolver.fund(code, institution)
        row.funds.append(TransactionEventFund(fund_id=fund.id, allocated_nominal=allocated))
    session.add(row)


def _write_snapshot(session: Session, resolver: EntityResolver, disc: Disclosure, snap) -> None:
    institution = resolver.institution(snap.market, snap.institution_ref, snap.institution_name)
    fund = resolver.fund(snap.fund_code, institution, snap.fund_name)
    disc.institution_id = institution.id
    # A corrected report (KAP düzeltme, 13F-HR/A) for the same date replaces the earlier snapshot.
    existing = session.scalar(
        select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == fund.id, PortfolioSnapshot.as_of == snap.as_of)
    )
    if existing is not None:
        session.delete(existing)
        session.flush()
    row = PortfolioSnapshot(
        fund_id=fund.id,
        disclosure_id=disc.id,
        as_of=snap.as_of,
        total_value=snap.total_value,
        source=snap.source,
        confidence=snap.confidence,
    )
    for h in snap.holdings:
        instrument = resolver.instrument(snap.market, h.instrument_symbol)
        row.holdings.append(
            SnapshotHolding(
                instrument_id=instrument.id,
                quantity=h.quantity,
                market_value=h.market_value,
                weight_pct=h.weight_pct,
            )
        )
    session.add(row)


# --------------------------------------------------------------------------- stage 3: positions


def rebuild_positions(session: Session) -> int:
    """Recompute every fund's position changes from its snapshot chain. Idempotent."""
    session.execute(delete(PositionChange))
    symbols = {i.id: i.symbol for i in session.scalars(select(Instrument))}
    ids_by_symbol = {v: k for k, v in symbols.items()}
    written = 0
    for fund in session.scalars(select(Fund)):
        snaps = session.scalars(
            select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == fund.id).order_by(PortfolioSnapshot.as_of)
        ).all()
        views = [
            SnapshotView(
                fund_code=fund.code,
                as_of=s.as_of,
                holdings=tuple(
                    HoldingView(symbols[h.instrument_id], h.quantity, h.market_value, h.weight_pct) for h in s.holdings
                ),
            )
            for s in snaps
        ]
        # The first snapshot is a baseline: it tells us what the fund holds, not what it bought.
        for prev, curr, prev_row, curr_row in zip(views, views[1:], snaps, snaps[1:], strict=False):
            prices = _closes_at(session, curr.as_of)
            for d in diff_snapshots(prev, curr, prices):
                session.add(
                    PositionChange(
                        fund_id=fund.id,
                        instrument_id=ids_by_symbol[d.instrument_symbol],
                        from_snapshot_id=prev_row.id,
                        to_snapshot_id=curr_row.id,
                        period_start=d.period_start,
                        period_end=d.period_end,
                        from_qty=d.from_qty,
                        to_qty=d.to_qty,
                        delta_qty=d.delta_qty,
                        from_weight_pct=d.from_weight_pct,
                        to_weight_pct=d.to_weight_pct,
                        delta_value=d.delta_value,
                        activity=d.activity,
                        confidence=d.confidence,
                    )
                )
                written += 1
    session.flush()
    return written


def _closes_at(session: Session, on: date) -> dict[str, Decimal]:
    """Latest close on or before `on`, per symbol."""
    latest = (
        select(MarketPrice.instrument_id, func.max(MarketPrice.trade_date).label("d"))
        .where(MarketPrice.trade_date <= on)
        .group_by(MarketPrice.instrument_id)
        .subquery()
    )
    rows = session.execute(
        select(Instrument.symbol, MarketPrice.close)
        .join(latest, (MarketPrice.instrument_id == latest.c.instrument_id) & (MarketPrice.trade_date == latest.c.d))
        .join(Instrument, Instrument.id == MarketPrice.instrument_id)
    )
    return {symbol: close for symbol, close in rows}


# --------------------------------------------------------------------------- stage 4: intelligence


# Activity window per market: KAP reports monthly + same-day events; 13F is quarterly with up to a
# 45-day filing lag, so a 30-day window would be empty most of the time.
MARKET_WINDOW_DAYS: dict[str, int] = {"TR": 30, "US": 100}


def compute_intelligence(session: Session, as_of: date, window_days: int | None = None) -> int:
    """Recompute scores and signals for every instrument with activity in its market's window."""
    max_window = window_days or max(MARKET_WINDOW_DAYS.values())
    earliest = as_of - timedelta(days=max_window)
    session.execute(delete(Score).where(Score.as_of == as_of))
    session.execute(delete(Signal).where(Signal.window_end == as_of))
    market_of = dict(session.execute(select(Instrument.id, Instrument.market_code)).all())

    def start_for(instrument_id: int) -> date:
        return as_of - timedelta(days=window_days or MARKET_WINDOW_DAYS.get(market_of.get(instrument_id, "TR"), 30))

    changes = [
        c for c in session.scalars(select(PositionChange).where(PositionChange.period_end > earliest, PositionChange.period_end <= as_of))
        if c.period_end > start_for(c.instrument_id)
    ]
    events = [
        e for e in session.scalars(
            select(TransactionEvent).where(
                TransactionEvent.is_superseded.is_(False),
                TransactionEvent.effective_date > earliest,
                TransactionEvent.effective_date <= as_of,
            )
        )
        if e.effective_date > start_for(e.instrument_id)
    ]
    latest_snapshot_by_fund = {
        fund_id: as_of_date
        for fund_id, as_of_date in session.execute(
            select(PortfolioSnapshot.fund_id, func.max(PortfolioSnapshot.as_of)).group_by(PortfolioSnapshot.fund_id)
        )
    }

    by_instrument: dict[int, list[PositionChange]] = defaultdict(list)
    for c in changes:
        by_instrument[c.instrument_id].append(c)
    events_by_instrument: dict[int, list[TransactionEvent]] = defaultdict(list)
    for e in events:
        if _event_is_uncovered(e, latest_snapshot_by_fund):
            events_by_instrument[e.instrument_id].append(e)

    written = 0
    for instrument_id in set(by_instrument) | set(events_by_instrument):
        activity = _instrument_activity(session, instrument_id, by_instrument[instrument_id], events_by_instrument[instrument_id], as_of)
        instrument = session.get(Instrument, instrument_id)
        assert instrument is not None
        sm = scoring.smart_money_score(activity)
        cs = scoring.consensus_score(activity)
        summary = _activity_summary(activity)
        session.add(_score_row(instrument_id, None, ScoreType.SMART_MONEY, as_of, sm, summary))
        session.add(_score_row(instrument_id, None, ScoreType.CONSENSUS, as_of, cs, summary))
        for c in by_instrument[instrument_id]:
            if c.activity in (ActivityType.ADD, ActivityType.NEW):
                conv = scoring.conviction_score(c.from_weight_pct, c.to_weight_pct)
                session.add(_score_row(instrument_id, c.fund_id, ScoreType.CONVICTION, as_of, conv, {}))
        for sig in _detect_signals(session, instrument_id, by_instrument[instrument_id], activity, start_for(instrument_id), as_of):
            # One row per signal EPISODE: if the same signal was already open for this instrument (its window_end
            # is the previous compute day or later), extend it instead of inserting a duplicate every day.
            open_row = session.scalar(
                select(Signal)
                .where(Signal.instrument_id == instrument_id, Signal.fund_id.is_(None), Signal.signal_type == sig.signal_type,
                       Signal.window_end >= as_of - timedelta(days=7), Signal.window_end < as_of)
                .order_by(Signal.window_end.desc()).limit(1)
            )
            if open_row is not None:
                open_row.window_end, open_row.strength, open_row.evidence, open_row.confidence = as_of, sig.strength, sig.evidence, sig.confidence
                continue
            session.add(
                Signal(
                    market_code=instrument.market_code,
                    instrument_id=instrument_id,
                    fund_id=None,
                    signal_type=sig.signal_type,
                    strength=sig.strength,
                    window_start=sig.window_start,
                    window_end=as_of,
                    evidence=sig.evidence,
                    confidence=sig.confidence,
                )
            )
        written += 1
    session.flush()
    return written


def _event_is_uncovered(e: TransactionEvent, latest_snapshot_by_fund: dict[int, date]) -> bool:
    """De-duplication law: a transaction event only counts while no later snapshot of any related
    fund exists. Once a snapshot covers the trade date, the snapshot diff is the canonical record.
    Conservative on purpose — we would rather undercount than double count."""
    covers = [latest_snapshot_by_fund.get(f.fund_id) for f in e.funds]
    covers = [d for d in covers if d is not None]
    return not covers or e.effective_date > max(covers)


def _instrument_activity(
    session: Session,
    instrument_id: int,
    changes: list[PositionChange],
    events: list[TransactionEvent],
    as_of: date,
) -> scoring.InstrumentActivity:
    # Breadth counts *parties*: a fund seen in snapshot diffs, an EXACT event's fund (same key, so
    # it is never double counted), or — for GROUPED events — the institution as one party.
    inc: set[str] = {f"fund:{c.fund_id}" for c in changes if c.activity in (ActivityType.ADD, ActivityType.NEW)}
    red: set[str] = {f"fund:{c.fund_id}" for c in changes if c.activity in (ActivityType.REDUCE, ActivityType.EXIT)}
    hold: set[str] = {f"fund:{c.fund_id}" for c in changes if c.activity == ActivityType.HOLD}
    new = {c.fund_id for c in changes if c.activity == ActivityType.NEW}
    exited = {c.fund_id for c in changes if c.activity == ActivityType.EXIT}

    flow_by_conf: dict[Confidence, Decimal] = defaultdict(Decimal)
    net_flow = Decimal(0)
    for c in changes:
        if c.delta_value is not None:
            net_flow += c.delta_value
            flow_by_conf[Confidence.INFERRED] += abs(c.delta_value)
    from instilens.services.analytics import event_value

    for e in events:
        value = event_value(session, e)
        if value is not None:
            net_flow += value
            flow_by_conf[Confidence(e.confidence)] += abs(value)
        party = f"fund:{e.funds[0].fund_id}" if e.confidence == Confidence.EXACT else f"inst:{e.institution_id}"
        if e.net_nominal > 0:
            inc.add(party)
        elif e.net_nominal < 0:
            red.add(party)

    convictions = [
        scoring.conviction_score(c.from_weight_pct, c.to_weight_pct).adjusted / 100
        for c in changes
        if c.activity in (ActivityType.ADD, ActivityType.NEW)
    ]
    last_dates = [c.period_end for c in changes] + [e.effective_date for e in events]
    return scoring.InstrumentActivity(
        funds_increasing=len(inc),
        funds_reducing=len(red),
        funds_unchanged=len(hold - inc - red),
        funds_new=len(new),
        funds_exited=len(exited),
        net_flow_value=net_flow,
        persistence_periods=_persistence(session, instrument_id, as_of),
        avg_conviction=sum(convictions) / len(convictions) if convictions else 0.0,
        days_since_last_activity=(as_of - max(last_dates)).days if last_dates else 365,
        flow_by_confidence=dict(flow_by_conf),
    )


def _period_flows(session: Session, instrument_id: int, as_of: date) -> list[PeriodFlow]:
    rows = session.execute(
        select(
            PositionChange.period_end,
            func.sum(PositionChange.delta_qty),
            func.sum(case((PositionChange.delta_qty > 0, 1), else_=0)),
            func.sum(case((PositionChange.delta_qty < 0, 1), else_=0)),
        )
        .where(PositionChange.instrument_id == instrument_id, PositionChange.period_end <= as_of)
        .group_by(PositionChange.period_end)
        .order_by(PositionChange.period_end)
    )
    return [PeriodFlow(period_end=d, net_qty=int(n or 0), funds_increasing=int(i or 0), funds_reducing=int(r or 0)) for d, n, i, r in rows]


def _persistence(session: Session, instrument_id: int, as_of: date) -> int:
    flows = _period_flows(session, instrument_id, as_of)
    count = 0
    sign = 0
    for f in reversed(flows):
        s = (f.net_qty > 0) - (f.net_qty < 0)
        if s == 0 or (sign and s != sign):
            break
        sign = s
        count += 1
    return count if sign > 0 else 0


def _detect_signals(session, instrument_id, changes, activity, window_start, as_of):
    out = []
    if sig := detect_accumulation(_period_flows(session, instrument_id, as_of)):
        out.append(sig)
    moves = [FundMove(str(c.fund_id), ActivityType(c.activity), c.period_end) for c in changes]
    for kind in (ActivityType.NEW, ActivityType.EXIT):
        if sig := detect_cluster(moves, kind):
            sig.evidence["funds"] = _fund_codes(session, [int(f) for f in sig.evidence["funds"]])
            out.append(sig)
    price_change = _price_change_pct(session, instrument_id, window_start, as_of)
    from_qty = sum(c.from_qty for c in changes)
    to_qty = sum(c.to_qty for c in changes)
    if price_change is not None and from_qty > 0:
        holdings_change = (Decimal(to_qty - from_qty) / Decimal(from_qty)) * 100
        if sig := detect_divergence(price_change, holdings_change, window_start, as_of, activity.funds_increasing, activity.funds_reducing):
            out.append(sig)
    return out


def _fund_codes(session: Session, fund_ids: list[int]) -> list[str]:
    return sorted(session.scalars(select(Fund.code).where(Fund.id.in_(fund_ids))))


def _price_change_pct(session: Session, instrument_id: int, start: date, end: date) -> Decimal | None:
    def close_at(on: date) -> Decimal | None:
        return session.scalar(
            select(MarketPrice.close)
            .where(MarketPrice.instrument_id == instrument_id, MarketPrice.trade_date <= on)
            .order_by(MarketPrice.trade_date.desc())
            .limit(1)
        )

    p0, p1 = close_at(start), close_at(end)
    if p0 is None or p1 is None or p0 == 0:
        return None
    return (p1 - p0) / p0 * 100


def _activity_summary(a: scoring.InstrumentActivity) -> dict:
    return {
        "funds_increasing": a.funds_increasing,
        "funds_reducing": a.funds_reducing,
        "funds_unchanged": a.funds_unchanged,
        "funds_new": a.funds_new,
        "funds_exited": a.funds_exited,
        "net_flow_value": str(a.net_flow_value),
        "persistence_periods": a.persistence_periods,
        "avg_conviction": round(a.avg_conviction, 4),
        "days_since_last_activity": a.days_since_last_activity,
        "flow_by_confidence": {k.value: str(v) for k, v in a.flow_by_confidence.items()},
    }


def _score_row(instrument_id, fund_id, score_type, as_of, breakdown, activity_summary) -> Score:
    components = breakdown.as_json()
    if activity_summary:
        components["activity"] = activity_summary
    return Score(
        instrument_id=instrument_id,
        fund_id=fund_id,
        score_type=score_type,
        as_of=as_of,
        raw_score=Decimal(str(round(breakdown.raw, 2))),
        adjusted_score=Decimal(str(round(breakdown.adjusted, 2))),
        components=components,
    )


# --------------------------------------------------------------------------- market data


def load_prices_csv(session: Session, path, market: Market = Market.TR) -> int:
    """Load `symbol,date,close[,volume]` rows. Prototype source; production uses a licensed feed."""
    resolver = EntityResolver(session)
    resolver.ensure_markets()
    n = 0
    with open(path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            instrument = resolver.instrument(market, row["symbol"])
            trade_date = date.fromisoformat(row["date"])
            existing = session.get(MarketPrice, (instrument.id, trade_date))
            close = Decimal(row["close"])
            if existing:
                existing.close = close
            else:
                session.add(MarketPrice(instrument_id=instrument.id, trade_date=trade_date, close=close))
            n += 1
    session.flush()
    return n


# --------------------------------------------------------------------------- convenience


def run_all(session: Session, adapter: SourceAdapter, as_of: date | None = None, prices_csv=None, market: Market = Market.TR) -> dict[str, int]:
    as_of = as_of or date.today()
    return {
        "prices": load_prices_csv(session, prices_csv, market) if prices_csv else 0,
        "ingested": ingest(session, adapter),
        "parsed": parse_pending(session),
        "position_changes": rebuild_positions(session),
        "instruments_scored": compute_intelligence(session, as_of),
        "notifications": _evaluate_alerts(session, as_of),
        "signal_outcomes": _compute_outcomes(session),
    }


def _compute_outcomes(session: Session) -> int:
    from instilens.services.outcomes import compute_outcomes

    return compute_outcomes(session)


def _evaluate_alerts(session: Session, as_of: date) -> int:
    from instilens.services.alerts import evaluate

    return evaluate(session, as_of)
