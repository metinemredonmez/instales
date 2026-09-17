"""Read models for the API. Pure queries; all numbers come from tables written by the pipeline."""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from typing import Literal, get_args

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session, selectinload

from instilens.domain.enums import ActivityType, Confidence, ScoreType
from instilens.domain.models import (
    Disclosure,
    Fund,
    Institution,
    Instrument,
    PortfolioSnapshot,
    PositionChange,
    Score,
    Signal,
    SnapshotHolding,
    TransactionEvent,
)
from instilens.services import fundamentals, insiders


def close_on_or_before(session: Session, instrument_id: int, on: date):
    from instilens.domain.models import MarketPrice

    return session.scalar(
        select(MarketPrice.close).where(MarketPrice.instrument_id == instrument_id, MarketPrice.trade_date <= on)
        .order_by(MarketPrice.trade_date.desc()).limit(1)
    )


def event_value(session: Session, ev: TransactionEvent):
    """Disclosed value if the filing carried a price, else nominal × close on the trade date (None if no price)."""
    if ev.net_value is not None:
        return ev.net_value
    close = close_on_or_before(session, ev.instrument_id, ev.effective_date)
    return ev.net_nominal * close if close is not None else None


def latest_score_date(session: Session) -> date | None:
    return session.scalar(select(func.max(Score.as_of)))


def radar(session: Session, market: str, limit: int = 20) -> dict:
    as_of = latest_score_date(session)
    if as_of is None:
        return {"as_of": None, "accumulated": [], "distributed": [], "signals": []}
    from instilens.services.pipeline import MARKET_WINDOW_DAYS

    window_days = MARKET_WINDOW_DAYS.get(market, 30)
    rows = session.execute(
        select(Instrument, Score)
        .join(Score, Score.instrument_id == Instrument.id)
        .where(Score.as_of == as_of, Score.score_type == ScoreType.SMART_MONEY, Instrument.market_code == market)
    ).all()
    consensus = {
        s.instrument_id: float(s.adjusted_score)
        for s in session.scalars(select(Score).where(Score.as_of == as_of, Score.score_type == ScoreType.CONSENSUS))
    }
    items = []
    for instrument, score in rows:
        act = score.components.get("activity", {})
        items.append(
            {
                "symbol": instrument.symbol,
                "name": instrument.name,
                "smart_money_score": float(score.adjusted_score),
                "consensus_score": consensus.get(instrument.id),
                "net_flow_value": float(act.get("net_flow_value", 0)),
                "funds_increasing": act.get("funds_increasing", 0),
                "funds_reducing": act.get("funds_reducing", 0),
                "funds_new": act.get("funds_new", 0),
                "funds_exited": act.get("funds_exited", 0),
                "confidence_multiplier": score.components.get("confidence_multiplier"),
            }
        )
    accumulated = sorted((i for i in items if i["net_flow_value"] > 0), key=lambda i: -i["net_flow_value"])[:limit]
    distributed = sorted((i for i in items if i["net_flow_value"] < 0), key=lambda i: i["net_flow_value"])[:limit]
    signals = session.execute(
        select(Signal, Instrument.symbol)
        .join(Instrument, Instrument.id == Signal.instrument_id)
        .where(Signal.window_end == as_of, Instrument.market_code == market)
        .order_by(Signal.strength.desc())
        .limit(limit)
    ).all()
    return {
        "as_of": as_of.isoformat(),
        "window_days": window_days,
        "accumulated": accumulated,
        "distributed": distributed,
        "signals": [_signal_json(s, symbol) for s, symbol in signals],
    }


def stock_detail(session: Session, market: str, symbol: str) -> dict | None:
    instrument = session.scalar(
        select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol.upper())
    )
    if instrument is None:
        return None
    as_of = latest_score_date(session)
    scores = {
        s.score_type: {"score": float(s.adjusted_score), "raw": float(s.raw_score), "why": s.components}
        for s in session.scalars(
            select(Score).where(Score.instrument_id == instrument.id, Score.as_of == as_of, Score.fund_id.is_(None))
        )
    }
    changes = session.execute(
        select(PositionChange, Fund.code)
        .join(Fund, Fund.id == PositionChange.fund_id)
        .where(PositionChange.instrument_id == instrument.id)
        .order_by(PositionChange.period_end.desc(), PositionChange.delta_qty.desc())
    ).all()
    latest_period = changes[0][0].period_end if changes else None
    recent = [(c, code) for c, code in changes if c.period_end == latest_period]
    signals = session.scalars(
        select(Signal).where(Signal.instrument_id == instrument.id).order_by(Signal.window_end.desc(), Signal.strength.desc())
    ).all()
    events = _events(session, instrument_id=instrument.id, limit=20)
    return {
        "symbol": instrument.symbol,
        "name": instrument.name,
        "market": instrument.market_code,
        "as_of": as_of.isoformat() if as_of else None,
        "scores": scores,
        "latest_period_end": latest_period.isoformat() if latest_period else None,
        "top_buyers": [_change_json(c, code) for c, code in recent if c.delta_qty > 0][:10],
        "top_sellers": [_change_json(c, code) for c, code in sorted(recent, key=lambda x: x[0].delta_qty) if c.delta_qty < 0][:10],
        "signals": [_signal_json(s, instrument.symbol) for s in signals],
        "events": events,
        "fundamentals": fundamentals.summary(session, instrument.id),  # null until the weekly job has run for the symbol
        "insiders": insiders.detail(session, instrument),  # US only; null until the daily Form 4 job has read the issuer
    }


def fund_detail(session: Session, code: str) -> dict | None:
    fund = session.scalar(select(Fund).where(Fund.code == code.upper()))
    if fund is None:
        return None
    snapshot = session.scalar(
        select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == fund.id).order_by(PortfolioSnapshot.as_of.desc()).limit(1)
    )
    holdings = []
    if snapshot:
        holdings = [
            {
                "symbol": symbol,
                "quantity": h.quantity,
                "market_value": float(h.market_value) if h.market_value is not None else None,
                "weight_pct": float(h.weight_pct) if h.weight_pct is not None else None,
            }
            for h, symbol in session.execute(
                select(SnapshotHolding, Instrument.symbol)
                .join(Instrument, Instrument.id == SnapshotHolding.instrument_id)
                .where(SnapshotHolding.snapshot_id == snapshot.id)
                .order_by(SnapshotHolding.market_value.desc().nulls_last())
            )
        ]
    changes = session.execute(
        select(PositionChange, Instrument.symbol)
        .join(Instrument, Instrument.id == PositionChange.instrument_id)
        .where(PositionChange.fund_id == fund.id)
        .order_by(PositionChange.period_end.desc(), PositionChange.delta_qty.desc())
    ).all()
    latest_period = changes[0][0].period_end if changes else None
    activity = {a.value: [] for a in ActivityType if a is not ActivityType.HOLD}
    for c, symbol in changes:
        if c.period_end == latest_period and c.activity in activity:
            activity[c.activity].append(_change_json(c, symbol))
    return {
        "code": fund.code,
        "name": fund.name,
        "institution": {"code": fund.institution.code, "name": fund.institution.name},
        "snapshot_as_of": snapshot.as_of.isoformat() if snapshot else None,
        "total_value": float(snapshot.total_value) if snapshot and snapshot.total_value is not None else None,
        "holdings": holdings,
        "activity_period_end": latest_period.isoformat() if latest_period else None,
        "activity": activity,
        "events": _events(session, fund_id=fund.id, limit=20),
    }


def events(session: Session, market: str, limit: int = 50, after_id: int | None = None) -> list[dict]:
    return _events(session, market=market, limit=limit, after_id=after_id)


def _events(session, *, market=None, instrument_id=None, fund_id=None, limit=50, after_id=None) -> list[dict]:
    stmt = (
        select(TransactionEvent, Instrument.symbol, Institution.name, Disclosure.source_id, Disclosure.raw_uri)
        .join(Instrument, Instrument.id == TransactionEvent.instrument_id)
        .join(Institution, Institution.id == TransactionEvent.institution_id)
        .join(Disclosure, Disclosure.id == TransactionEvent.disclosure_id)
        .where(TransactionEvent.is_superseded.is_(False))
    )
    if market:
        stmt = stmt.where(TransactionEvent.market_code == market)
    if instrument_id:
        stmt = stmt.where(TransactionEvent.instrument_id == instrument_id)
    if fund_id:
        stmt = stmt.where(TransactionEvent.funds.any(fund_id=fund_id))
    if after_id:
        stmt = stmt.where(TransactionEvent.id > after_id).order_by(TransactionEvent.id.asc())
    else:
        stmt = stmt.order_by(TransactionEvent.published_at.desc())
    out = []
    for ev, symbol, institution_name, source_id, raw_uri in session.execute(stmt.limit(limit)):
        codes = session.scalars(select(Fund.code).where(Fund.id.in_([f.fund_id for f in ev.funds]))).all()
        out.append(
            {
                "id": ev.id,
                "published_at": ev.published_at.isoformat(),
                "effective_date": ev.effective_date.isoformat(),
                "symbol": symbol,
                "institution": institution_name,
                "funds": sorted(codes),
                "side": ev.side,
                "buy_nominal": ev.buy_nominal,
                "sell_nominal": ev.sell_nominal,
                "net_nominal": ev.net_nominal,
                "net_value": (lambda v: float(v) if v is not None else None)(event_value(session, ev)),
                "value_source": "disclosed" if ev.net_value is not None else "close_price",
                "ownership_before_pct": float(ev.ownership_before_pct) if ev.ownership_before_pct is not None else None,
                "ownership_after_pct": float(ev.ownership_after_pct) if ev.ownership_after_pct is not None else None,
                "confidence": ev.confidence,
                "allocation": "EXACT" if ev.confidence == "EXACT" else "UNKNOWN",
                "source": {"name": "KAP", "id": source_id, "uri": raw_uri},
            }
        )
    return out


def _change_json(c: PositionChange, label: str) -> dict:
    return {
        "fund": label if c.fund_id else None,
        "symbol": label,
        "activity": c.activity,
        "period_start": c.period_start.isoformat() if c.period_start else None,
        "period_end": c.period_end.isoformat(),
        "from_qty": c.from_qty,
        "to_qty": c.to_qty,
        "delta_qty": c.delta_qty,
        "delta_value": float(c.delta_value) if c.delta_value is not None else None,
        "from_weight_pct": float(c.from_weight_pct) if c.from_weight_pct is not None else None,
        "to_weight_pct": float(c.to_weight_pct) if c.to_weight_pct is not None else None,
        "delta_weight_pct": float(c.delta_weight_pct) if c.delta_weight_pct is not None else None,
        "from_value": float(c.from_value) if c.from_value is not None else None,
        "to_value": float(c.to_value) if c.to_value is not None else None,
        "pct_change_qty": float(c.pct_change_qty) if c.pct_change_qty is not None else None,
        "gap_periods": c.gap_periods,
        "confidence": c.confidence,
    }


def _signal_json(s: Signal, symbol: str) -> dict:
    return {
        "symbol": symbol,
        "type": s.signal_type,
        "strength": s.strength,
        "window_start": s.window_start.isoformat(),
        "window_end": s.window_end.isoformat(),
        "confidence": s.confidence,
        "evidence": s.evidence,
    }


def screener(
    session: Session,
    market: str,
    *,
    min_smart_money_score: float | None = None,
    min_consensus_score: float | None = None,
    min_funds_increasing: int | None = None,
    min_funds_new: int | None = None,
    min_net_flow_value: float | None = None,
    max_price_change_pct: float | None = None,
    signal_types: list[str] | None = None,
    limit: int = 25,
) -> list[dict]:
    """Smart Money Screener over the latest score snapshot. Every filter is AND-ed."""
    as_of = latest_score_date(session)
    if as_of is None:
        return []
    signals_by_instrument: dict[int, list[str]] = {}
    for sig in session.scalars(select(Signal).where(Signal.window_end == as_of)):
        signals_by_instrument.setdefault(sig.instrument_id, []).append(sig.signal_type)
    consensus = {
        s.instrument_id: float(s.adjusted_score)
        for s in session.scalars(select(Score).where(Score.as_of == as_of, Score.score_type == ScoreType.CONSENSUS))
    }
    out = []
    for instrument, score in session.execute(
        select(Instrument, Score)
        .join(Score, Score.instrument_id == Instrument.id)
        .where(Score.as_of == as_of, Score.score_type == ScoreType.SMART_MONEY, Instrument.market_code == market)
    ):
        act = score.components.get("activity", {})
        row = {
            "symbol": instrument.symbol,
            "name": instrument.name,
            "smart_money_score": float(score.adjusted_score),
            "consensus_score": consensus.get(instrument.id),
            "funds_increasing": act.get("funds_increasing", 0),
            "funds_reducing": act.get("funds_reducing", 0),
            "funds_new": act.get("funds_new", 0),
            "funds_exited": act.get("funds_exited", 0),
            "net_flow_value": float(act.get("net_flow_value", 0)),
            "price_change_30d_pct": _price_change_30d(session, instrument.id, as_of),
            "signals": sorted(signals_by_instrument.get(instrument.id, [])),
        }
        if min_smart_money_score is not None and row["smart_money_score"] < min_smart_money_score:
            continue
        if min_consensus_score is not None and (row["consensus_score"] or 0) < min_consensus_score:
            continue
        if min_funds_increasing is not None and row["funds_increasing"] < min_funds_increasing:
            continue
        if min_funds_new is not None and row["funds_new"] < min_funds_new:
            continue
        if min_net_flow_value is not None and row["net_flow_value"] < min_net_flow_value:
            continue
        if max_price_change_pct is not None and (
            row["price_change_30d_pct"] is None or row["price_change_30d_pct"] > max_price_change_pct
        ):
            continue
        if signal_types and not set(signal_types) & set(row["signals"]):
            continue
        out.append(row)
    out.sort(key=lambda r: -r["smart_money_score"])
    return out[:limit]


def _price_change_30d(session: Session, instrument_id: int, as_of: date) -> float | None:
    from datetime import timedelta

    from instilens.domain.models import MarketPrice

    def close_at(on: date):
        return session.scalar(
            select(MarketPrice.close)
            .where(MarketPrice.instrument_id == instrument_id, MarketPrice.trade_date <= on)
            .order_by(MarketPrice.trade_date.desc())
            .limit(1)
        )

    p0, p1 = close_at(as_of - timedelta(days=30)), close_at(as_of)
    if p0 is None or p1 is None or p0 == 0:
        return None
    return round(float((p1 - p0) / p0 * 100), 2)


def stock_series(session: Session, market: str, symbol: str) -> dict | None:
    """Price closes and aggregate institutional holdings (sum of fund quantities per snapshot date)."""
    from instilens.domain.models import MarketPrice, PortfolioSnapshot, SnapshotHolding

    instrument = session.scalar(select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol.upper()))
    if instrument is None:
        return None
    prices = [
        {"date": d.isoformat(), "close": float(c)}
        for d, c in session.execute(select(MarketPrice.trade_date, MarketPrice.close).where(MarketPrice.instrument_id == instrument.id).order_by(MarketPrice.trade_date))
    ]
    holdings = [
        {"date": d.isoformat(), "quantity": int(q or 0), "funds": int(n or 0)}
        for d, q, n in session.execute(
            select(PortfolioSnapshot.as_of, func.sum(SnapshotHolding.quantity), func.count(SnapshotHolding.snapshot_id))
            .join(SnapshotHolding, SnapshotHolding.snapshot_id == PortfolioSnapshot.id)
            .where(SnapshotHolding.instrument_id == instrument.id)
            .group_by(PortfolioSnapshot.as_of)
            .order_by(PortfolioSnapshot.as_of)
        )
    ]
    return {"symbol": instrument.symbol, "prices": prices, "holdings": holdings}


# --------------------------------------------------------------------------- windows / timeline / compare / institutions


def window_flows(session: Session, market: str, window_days: int, as_of: date | None = None) -> dict:
    """Net institutional flow per instrument for an arbitrary window, straight from the fact tables.

    Scores use each market's own window (`pipeline.MARKET_WINDOW_DAYS`: 30 days TR, 100 days US); this powers
    the Today/7D/3M leaderboards for any window. Party counting follows the breadth law (a fund from a snapshot
    diff or an EXACT event is one party, a GROUPED event is its institution) and the de-dup law of the score
    engine: snapshot diffs are canonical, an event counts only after the latest snapshot known on `as_of`.
    `funds_new` / `funds_exited` count the same parties the score engine counts — a snapshot diff's NEW/EXIT plus
    the first-time entries and full exits disclosed by uncovered events.
    """
    as_of = as_of or latest_score_date(session) or date.today()
    start = as_of - timedelta(days=window_days)
    rows = [_flow_row(a) for a in _window_activity(session, market, start, as_of).values()]
    return {"as_of": as_of.isoformat(), "window_days": window_days, "window_start": start.isoformat(),
            "accumulated": sorted((r for r in rows if r["net_flow_value"] > 0), key=lambda r: (-r["net_flow_value"], r["symbol"])),
            "distributed": sorted((r for r in rows if r["net_flow_value"] < 0), key=lambda r: (r["net_flow_value"], r["symbol"]))}


MoveKind = Literal["buys", "sells", "new", "exits"]
MOVE_KINDS: tuple[str, ...] = get_args(MoveKind)
# Which rows a kind keeps and how it ranks them: by net flow at market scope, by the fund's own activity at fund scope.
# Every order ends on the symbol so ties (unpriced rows, equal party counts) cut the same way on every request.
_MARKET_SCOPE_KEEP = {
    "buys": lambda r: r["net_flow_value"] > 0,
    "sells": lambda r: r["net_flow_value"] < 0,
    "new": lambda r: r["funds_new"] > 0,
    "exits": lambda r: r["funds_exited"] > 0,
}
_FUND_SCOPE_ACTIVITIES = {"buys": {"ADD", "NEW"}, "sells": {"REDUCE", "EXIT"}, "new": {"NEW"}, "exits": {"EXIT"}}
_MOVE_ORDER = {
    "buys": lambda r: (-r["net_flow_value"], r["symbol"]),
    "sells": lambda r: (r["net_flow_value"], r["symbol"]),
    "new": lambda r: (-r["funds_new"], -r["net_flow_value"], r["symbol"]),
    "exits": lambda r: (-r["funds_exited"], r["net_flow_value"], r["symbol"]),
}


def moves(session: Session, market: str, *, kind: str, window_days: int | None = None, fund_code: str | None = None, limit: int = 25) -> dict | None:
    """The window's biggest moves, one row per instrument with the parties behind it (`/moves`, the AI tools).

    Same aggregation as `window_flows`, plus who moved: one party entry per breadth-law party, at most five per
    row, largest |delta_value| first. buys / sells rank by net flow; new / exits rank by how many parties entered
    or left (their party lists keep only the NEW / EXIT parties; a row whose only entrant left again in the same
    window folds to an EXIT party and is dropped from `new`, so every row names at least one party). `total` is
    the row count before the `limit` cut. With `fund_code` the rows are that fund's own moves — its snapshot
    diffs and its EXACT events — so every row has exactly that fund as its single party; None when the fund is
    unknown in this market (the route answers 404). Values are in the market currency.
    """
    if kind not in MOVE_KINDS:
        raise ValueError(f"unknown move kind {kind!r}")
    from instilens.services.pipeline import MARKET_WINDOW_DAYS

    fund = None
    if fund_code is not None:
        fund = session.scalar(select(Fund).join(Institution).where(Fund.code == fund_code.upper(), Institution.market_code == market))
        if fund is None:
            return None
    window_days = window_days or MARKET_WINDOW_DAYS.get(market, 30)
    as_of = latest_score_date(session) or date.today()
    start = as_of - timedelta(days=window_days)
    rows = []
    for a in _window_activity(session, market, start, as_of, fund=fund).values():
        row = _flow_row(a)
        parties = _parties(a)
        if fund is not None:  # the fund is the single party: its own move decides whether the row belongs to the kind
            if not parties or parties[0]["activity"] not in _FUND_SCOPE_ACTIVITIES[kind]:
                continue
        elif not _MARKET_SCOPE_KEEP[kind](row):
            continue
        if kind in ("new", "exits"):
            parties = [p for p in parties if p["activity"] == ("NEW" if kind == "new" else "EXIT")]
            if not parties:
                continue
        row["party_count"] = len(parties)
        row["parties"] = sorted(parties, key=lambda p: (-abs(p["delta_value"] or 0), p["kind"], p["ref"]))[:5]
        rows.append(row)
    rows.sort(key=_MOVE_ORDER[kind])
    total = len(rows)
    rows = rows[:limit]
    _name_parties(session, rows)
    return {
        "as_of": as_of.isoformat(), "window_days": window_days, "window_start": start.isoformat(), "kind": kind, "market": market,
        "fund": {"code": fund.code, "name": fund.name} if fund is not None else None,
        "total": total, "rows": rows,
    }


def _window_activity(session: Session, market: str, start: date, as_of: date, *, fund: Fund | None = None) -> dict[int, dict]:
    """The window's moves per instrument: snapshot diffs whose `period_end` falls in (start, as_of] and the
    transaction events the de-dup law lets through (an event counts only after the latest snapshot known on
    `as_of`). With `fund`: only that fund's diffs and its EXACT events — a GROUPED amount cannot be attributed
    to one fund. Each entry carries the Instrument row and every event's value (`event_value`, priced once here)
    too, so callers need no further queries. Rows come in id order so folding is repeatable across requests."""
    from instilens.services.pipeline import _event_is_uncovered, latest_snapshot_as_of

    changes = (
        select(PositionChange).join(Instrument, Instrument.id == PositionChange.instrument_id)
        .where(Instrument.market_code == market, PositionChange.period_end > start, PositionChange.period_end <= as_of)
        .order_by(PositionChange.id)
    )
    events = (
        select(TransactionEvent).options(selectinload(TransactionEvent.funds))  # the de-dup law reads every event's funds
        .where(
            TransactionEvent.market_code == market, TransactionEvent.is_superseded.is_(False),
            TransactionEvent.effective_date > start, TransactionEvent.effective_date <= as_of,
        )
        .order_by(TransactionEvent.id)
    )
    if fund is not None:
        changes = changes.where(PositionChange.fund_id == fund.id)
        events = events.where(TransactionEvent.confidence == Confidence.EXACT, TransactionEvent.funds.any(fund_id=fund.id))
    out: dict[int, dict] = defaultdict(lambda: {"changes": [], "events": [], "values": {}})
    for c in session.scalars(changes):
        out[c.instrument_id]["changes"].append(c)
    latest_snap = latest_snapshot_as_of(session, as_of)
    for e in session.scalars(events):
        if _event_is_uncovered(e, latest_snap):
            out[e.instrument_id]["events"].append(e)
            out[e.instrument_id]["values"][e.id] = event_value(session, e)
    instruments = {i.id: i for i in session.scalars(select(Instrument).where(Instrument.id.in_(out.keys())))} if out else {}
    for iid, a in out.items():
        a["instrument"] = instruments[iid]
    return out


def _flow_row(a: dict) -> dict:
    """One leaderboard row from an instrument's window moves: net flow, net quantity and the breadth-law party
    counts (`window_flows` documents the laws). Flow is summed as Decimal like the score engine does, so a window
    that nets to exactly zero lands in neither leaderboard; a zero-net (MIXED) event moves no party either."""
    from instilens.services.pipeline import _party_moves, event_party

    net_flow, net_qty, inc, red = Decimal(0), 0, set(), set()
    for c in a["changes"]:
        if c.delta_value is not None:
            net_flow += c.delta_value
        net_qty += c.delta_qty
        if c.activity in ("ADD", "NEW"):
            inc.add(f"fund:{c.fund_id}")
        elif c.activity in ("REDUCE", "EXIT"):
            red.add(f"fund:{c.fund_id}")
    for e in a["events"]:
        value = a["values"][e.id]
        if value is not None:
            net_flow += value
        net_qty += e.net_nominal
        if e.net_nominal > 0:
            inc.add(event_party(e))
        elif e.net_nominal < 0:
            red.add(event_party(e))
    snapshot_moves, event_moves = _party_moves(a["changes"], a["events"])
    all_moves = snapshot_moves + event_moves
    inst = a["instrument"]
    return {"symbol": inst.symbol, "name": inst.name, "net_flow_value": float(net_flow), "net_qty": net_qty,
            "funds_increasing": len(inc), "funds_reducing": len(red),
            "funds_new": len({m.fund_code for m in all_moves if m.activity is ActivityType.NEW}),
            "funds_exited": len({m.fund_code for m in all_moves if m.activity is ActivityType.EXIT})}


_WEAKER = {Confidence.EXACT: 0, Confidence.GROUPED: 1, Confidence.INFERRED: 2}


def _parties(a: dict) -> list[dict]:
    """Who moved: one entry per breadth-law party with its window moves folded together. Quantities and values add
    up; a NEW / EXIT move labels the party (the later one when it both entered and left — these are what
    `funds_new` / `funds_exited` count), otherwise the net quantity does. Weights come from the snapshot diffs
    (events carry none); confidence is the weakest source behind the entry. HOLD diffs and zero-net (MIXED)
    events are not moves, as in the score engine. Codes and names are filled in by `_name_parties` once the rows
    are cut to `limit`."""
    from instilens.services.pipeline import _party_moves, event_entries_exits, event_party

    snapshot_moves, _ = _party_moves(a["changes"], a["events"])
    entries_exits = {e.id: kind for e, kind in event_entries_exits(a["events"], snapshot_moves)}
    by_party: dict[str, list[dict]] = defaultdict(list)
    for c in a["changes"]:
        if c.activity != ActivityType.HOLD:
            by_party[f"fund:{c.fund_id}"].append({"on": c.period_end, "activity": ActivityType(c.activity), "delta_qty": c.delta_qty, "delta_value": c.delta_value,
                                                  "confidence": Confidence.INFERRED, "from_weight_pct": c.from_weight_pct, "to_weight_pct": c.to_weight_pct})
    for e in a["events"]:
        activity = entries_exits.get(e.id)
        if activity is None:
            if e.net_nominal == 0:
                continue
            activity = ActivityType.ADD if e.net_nominal > 0 else ActivityType.REDUCE
        by_party[event_party(e)].append({"on": e.effective_date, "activity": activity, "delta_qty": e.net_nominal, "delta_value": a["values"][e.id],
                                         "confidence": Confidence(e.confidence)})
    out = []
    for key, moves_ in by_party.items():
        moves_.sort(key=lambda m: m["on"])
        delta_qty = sum(m["delta_qty"] for m in moves_)
        values = [m["delta_value"] for m in moves_ if m["delta_value"] is not None]
        entries_exits_ = [m["activity"] for m in moves_ if m["activity"] in (ActivityType.NEW, ActivityType.EXIT)]
        activity = entries_exits_[-1] if entries_exits_ else ActivityType.ADD if delta_qty > 0 else ActivityType.REDUCE if delta_qty < 0 else moves_[-1]["activity"]
        diffs = [m for m in moves_ if "to_weight_pct" in m]
        from_w, to_w = (diffs[0]["from_weight_pct"], diffs[-1]["to_weight_pct"]) if diffs else (None, None)
        prefix, ref = key.split(":")
        out.append({
            "kind": "fund" if prefix == "fund" else "institution", "ref": int(ref), "activity": activity.value,
            "delta_qty": delta_qty, "delta_value": float(sum(values)) if values else None,
            "to_weight_pct": float(to_w) if to_w is not None else None,
            "delta_weight_pct": float(to_w - from_w) if to_w is not None and from_w is not None else None,
            "period_end": moves_[-1]["on"].isoformat(),
            "confidence": max((m["confidence"] for m in moves_), key=_WEAKER.__getitem__).value,
        })
    return out


def _name_parties(session: Session, rows: list[dict]) -> None:
    """Replace each party's `ref` (fund / institution id) with its code and name — one query per kind."""
    fund_ids = {p["ref"] for r in rows for p in r["parties"] if p["kind"] == "fund"}
    inst_ids = {p["ref"] for r in rows for p in r["parties"] if p["kind"] == "institution"}
    names = {}
    if fund_ids:
        names.update({("fund", f.id): (f.code, f.name) for f in session.scalars(select(Fund).where(Fund.id.in_(fund_ids)))})
    if inst_ids:
        names.update({("institution", i.id): (i.code, i.name) for i in session.scalars(select(Institution).where(Institution.id.in_(inst_ids)))})
    for r in rows:
        for p in r["parties"]:
            p["code"], p["name"] = names[(p["kind"], p.pop("ref"))]


def stock_timeline(session: Session, market: str, symbol: str) -> list[dict] | None:
    """The stock's smart-money story in order: period activity, signals, score changes, disclosed events."""
    from instilens.domain.models import PositionChange, TransactionEvent

    instrument = session.scalar(select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol.upper()))
    if instrument is None:
        return None
    items: list[dict] = []
    for period_end, n_inc, n_red, n_new, n_exit, qty in session.execute(
        select(
            PositionChange.period_end,
            func.sum(case((PositionChange.activity.in_(["ADD", "NEW"]), 1), else_=0)),
            func.sum(case((PositionChange.activity.in_(["REDUCE", "EXIT"]), 1), else_=0)),
            func.sum(case((PositionChange.activity == "NEW", 1), else_=0)),
            func.sum(case((PositionChange.activity == "EXIT", 1), else_=0)),
            func.sum(PositionChange.delta_qty),
        ).where(PositionChange.instrument_id == instrument.id).group_by(PositionChange.period_end)
    ):
        items.append({"date": period_end.isoformat(), "kind": "PERIOD", "title": f"{n_inc} fon artırdı · {n_red} azalttı", "detail": f"{n_new} yeni · {n_exit} çıkış · net {int(qty or 0):+,} lot", "confidence": "INFERRED"})
    for s in session.scalars(select(Signal).where(Signal.instrument_id == instrument.id)):
        items.append({"date": s.window_end.isoformat(), "kind": "SIGNAL", "title": s.signal_type, "detail": f"güç {s.strength}", "confidence": s.confidence, "signal_type": s.signal_type})
    for sc in session.scalars(select(Score).where(Score.instrument_id == instrument.id, Score.score_type == ScoreType.SMART_MONEY, Score.fund_id.is_(None)).order_by(Score.as_of)):
        items.append({"date": sc.as_of.isoformat(), "kind": "SCORE", "title": f"Smart Money Score → {float(sc.adjusted_score):.0f}", "detail": None, "confidence": None})
    for ev in session.scalars(select(TransactionEvent).where(TransactionEvent.instrument_id == instrument.id, TransactionEvent.is_superseded.is_(False))):
        inst = session.get(Institution, ev.institution_id)
        items.append({"date": ev.effective_date.isoformat(), "kind": "EVENT", "title": f"{inst.name}: {'alım' if ev.net_nominal > 0 else 'satım'} {ev.net_nominal:+,}", "detail": f"KAP #{session.get(Disclosure, ev.disclosure_id).source_id}", "confidence": ev.confidence})
    order = {"PERIOD": 0, "EVENT": 1, "SIGNAL": 2, "SCORE": 3}
    return sorted(items, key=lambda i: (i["date"], order[i["kind"]]))


def compare_funds(session: Session, code_a: str, code_b: str) -> dict | None:
    a = session.scalar(select(Fund).where(Fund.code == code_a.upper()))
    b = session.scalar(select(Fund).where(Fund.code == code_b.upper()))
    if a is None or b is None:
        return None

    def latest_holdings(fund: Fund) -> dict[str, dict]:
        snap = session.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == fund.id).order_by(PortfolioSnapshot.as_of.desc()).limit(1))
        if snap is None:
            return {}
        return {sym: {"quantity": h.quantity, "weight_pct": float(h.weight_pct) if h.weight_pct is not None else None}
                for h, sym in session.execute(select(SnapshotHolding, Instrument.symbol).join(Instrument, Instrument.id == SnapshotHolding.instrument_id).where(SnapshotHolding.snapshot_id == snap.id))}

    def latest_moves(fund: Fund) -> dict[str, str]:
        latest = session.scalar(select(func.max(PositionChange.period_end)).where(PositionChange.fund_id == fund.id))
        if latest is None:
            return {}
        return {sym: act for sym, act in session.execute(select(Instrument.symbol, PositionChange.activity).join(Instrument, Instrument.id == PositionChange.instrument_id).where(PositionChange.fund_id == fund.id, PositionChange.period_end == latest))}

    ha, hb, ma, mb = latest_holdings(a), latest_holdings(b), latest_moves(a), latest_moves(b)
    common = sorted(set(ha) & set(hb))
    up, down = {"ADD", "NEW"}, {"REDUCE", "EXIT"}
    both_inc = [s for s in set(ma) | set(mb) if ma.get(s) in up and mb.get(s) in up]
    both_red = [s for s in set(ma) | set(mb) if ma.get(s) in down and mb.get(s) in down]
    opposite = [{"symbol": s, "a": ma.get(s), "b": mb.get(s)} for s in set(ma) & set(mb) if (ma[s] in up and mb[s] in down) or (ma[s] in down and mb[s] in up)]
    return {
        "a": {"code": a.code, "name": a.name, "institution": a.institution.name}, "b": {"code": b.code, "name": b.name, "institution": b.institution.name},
        "common": [{"symbol": s, "a_weight_pct": ha[s]["weight_pct"], "b_weight_pct": hb[s]["weight_pct"], "a_move": ma.get(s), "b_move": mb.get(s)} for s in common],
        "only_a": sorted(set(ha) - set(hb)), "only_b": sorted(set(hb) - set(ha)),
        "both_increasing": sorted(both_inc), "both_reducing": sorted(both_red), "opposite": sorted(opposite, key=lambda o: o["symbol"]),
        "overlap_pct": round(100 * len(common) / max(len(set(ha) | set(hb)), 1), 1),
    }


def search(session: Session, market: str, q: str, limit: int = 8) -> list[dict]:
    """Typeahead over instruments, funds and institutions of a market.

    Ranking: exact symbol/code → symbol/code prefix → name substring. Returns at most `limit`
    rows per kind so a short query like "A" still shows funds and institutions, not only stocks.
    """
    q = q.strip()[:64]
    if not q:
        return []
    q = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")  # user text must not act as LIKE wildcards
    up = q.upper()
    like = f"%{q}%"
    rank = lambda code_col: case((code_col == up, 0), (code_col.like(f"{up}%", escape="\\"), 1), else_=2)  # noqa: E731

    stocks = session.scalars(
        select(Instrument)
        .where(Instrument.market_code == market, (Instrument.symbol.like(f"{up}%", escape="\\")) | (Instrument.name.ilike(like, escape="\\")))
        .order_by(rank(Instrument.symbol), Instrument.symbol)
        .limit(limit)
    )
    funds = session.scalars(
        select(Fund)
        .join(Institution)
        .where(Institution.market_code == market, (Fund.code.like(f"{up}%", escape="\\")) | (Fund.name.ilike(like, escape="\\")))
        .order_by(rank(Fund.code), Fund.code)
        .limit(limit)
    )
    insts = session.scalars(
        select(Institution)
        .where(Institution.market_code == market, (Institution.code.like(f"{up}%", escape="\\")) | (Institution.name.ilike(like, escape="\\")))
        .order_by(rank(Institution.code), Institution.name)
        .limit(limit)
    )
    out = [{"kind": "stock", "key": i.symbol, "label": i.symbol, "name": i.name, "href": f"/stocks/{i.symbol}"} for i in stocks]
    out += [{"kind": "fund", "key": f.code, "label": f.code, "name": f.name, "href": f"/funds/{f.code}"} for f in funds]
    out += [{"kind": "institution", "key": i.code, "label": i.name, "name": i.kind, "href": f"/institutions/{i.code}"} for i in insts]
    return out


def institutions(session: Session, market: str) -> list[dict]:
    out = []
    for inst in session.scalars(select(Institution).where(Institution.market_code == market).order_by(Institution.name)):
        n_funds = session.scalar(select(func.count(Fund.id)).where(Fund.institution_id == inst.id)) or 0
        n_events = session.scalar(select(func.count(TransactionEvent.id)).where(TransactionEvent.institution_id == inst.id, TransactionEvent.is_superseded.is_(False))) or 0
        out.append({"code": inst.code, "name": inst.name, "kind": inst.kind, "funds": n_funds, "events": n_events, "is_verified": inst.is_verified})
    return out


def institution_detail(session: Session, market: str, code: str) -> dict | None:
    inst = session.scalar(select(Institution).where(Institution.market_code == market, Institution.code == code.upper()))
    if inst is None:
        return None
    funds = session.scalars(select(Fund).where(Fund.institution_id == inst.id).order_by(Fund.code)).all()
    fund_ids = [f.id for f in funds]
    latest = session.scalar(select(func.max(PositionChange.period_end)).where(PositionChange.fund_id.in_(fund_ids))) if fund_ids else None
    agg = []
    if latest:
        for sym, qty, val, n_inc, n_red in session.execute(
            select(Instrument.symbol, func.sum(PositionChange.delta_qty), func.sum(PositionChange.delta_value),
                   func.sum(case((PositionChange.activity.in_(["ADD", "NEW"]), 1), else_=0)), func.sum(case((PositionChange.activity.in_(["REDUCE", "EXIT"]), 1), else_=0)))
            .join(Instrument, Instrument.id == PositionChange.instrument_id)
            .where(PositionChange.fund_id.in_(fund_ids), PositionChange.period_end == latest).group_by(Instrument.symbol)
        ):
            agg.append({"symbol": sym, "delta_qty": int(qty or 0), "delta_value": float(val or 0), "funds_increasing": int(n_inc or 0), "funds_reducing": int(n_red or 0)})
    return {
        "code": inst.code, "name": inst.name, "kind": inst.kind, "is_verified": inst.is_verified,
        "funds": [{"code": f.code, "name": f.name, "fund_type": f.fund_type} for f in funds],
        "activity_period_end": latest.isoformat() if latest else None,
        "top_increased": sorted((r for r in agg if r["delta_qty"] > 0), key=lambda r: -r["delta_value"])[:15],
        "top_reduced": sorted((r for r in agg if r["delta_qty"] < 0), key=lambda r: r["delta_value"])[:15],
        "events": [e for e in _events(session, market=market, limit=30) if e["institution"] == inst.name],
    }


def signal_performance(session: Session, market: str) -> dict:
    """Credibility layer: average forward returns per signal type, from signal_outcomes."""
    from instilens.domain.models import SignalOutcome

    rows = session.execute(
        select(Signal.signal_type, func.count(SignalOutcome.signal_id), func.avg(SignalOutcome.ret_7d), func.avg(SignalOutcome.ret_30d), func.avg(SignalOutcome.ret_90d), func.avg(SignalOutcome.max_drawdown))
        .join(SignalOutcome, SignalOutcome.signal_id == Signal.id).where(Signal.market_code == market).group_by(Signal.signal_type)
    ).all()
    return {"by_type": [{"signal_type": t, "count": int(n), "avg_ret_7d": _f(r7), "avg_ret_30d": _f(r30), "avg_ret_90d": _f(r90), "avg_max_drawdown": _f(dd)} for t, n, r7, r30, r90, dd in rows]}


def _f(v):
    return round(float(v), 2) if v is not None else None


def data_freshness(session: Session, market: str) -> list[dict]:
    """What the user must know before trusting a number: how old each source is."""
    from instilens.domain.models import MarketPrice, TransactionEvent
    from instilens.ingestion.prices.provider import resolve_provider

    def last(stmt):
        v = session.scalar(stmt)
        return v.isoformat() if v else None

    prices = resolve_provider().status()  # the cadence names whoever actually prints the bars
    prices_row = {"source": "Market prices", "cadence": f"Daily ({prices.name}, {prices.delay})", "last": last(select(func.max(MarketPrice.trade_date)).join(Instrument).where(Instrument.market_code == market)), "delayed": False}
    if market == "US":
        form4 = insiders.last_filed_at(session)
        return [
            {"source": "SEC 13F", "cadence": "Quarterly, up to 45 days after quarter end", "last": last(select(func.max(PortfolioSnapshot.as_of)).join(Fund).join(Institution).where(Institution.market_code == "US")), "delayed": True},
            {"source": "SEC Form 4", "cadence": "Within two business days of the trade", "last": form4.date().isoformat() if form4 else None, "delayed": False},
            prices_row,
        ]
    return [
        {"source": "KAP transaction disclosures", "cadence": "Same-day", "last": last(select(func.max(TransactionEvent.published_at)).where(TransactionEvent.market_code == "TR")), "delayed": False},
        {"source": "KAP portfolio reports", "cadence": "Monthly snapshot", "last": last(select(func.max(PortfolioSnapshot.as_of)).join(Fund).join(Institution).where(Institution.market_code == "TR")), "delayed": True},
        prices_row,
    ]


def news(session: Session, market: str, symbol: str | None = None, limit: int = 40) -> list[dict]:
    from instilens.domain.models import NewsItem

    stmt = select(NewsItem).where(NewsItem.market_code == market).order_by(NewsItem.published_at.desc()).limit(limit * (4 if symbol else 1))
    rows = session.scalars(stmt).all()
    if symbol:
        rows = [n for n in rows if symbol.upper() in (n.symbols or [])][:limit]
    return [
        {"id": n.id, "source": n.source, "title": n.title, "url": n.url, "published_at": n.published_at.isoformat(), "symbols": n.symbols or [], "tags": n.tags or [], "ai": n.ai}
        for n in rows
    ]
