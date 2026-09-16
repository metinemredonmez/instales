"""Read models for the API. Pure queries; all numbers come from tables written by the pipeline."""

from __future__ import annotations

from datetime import date

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from instilens.domain.enums import ActivityType, ScoreType
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

    Scores are always 30D; this powers the Today/7D/3M leaderboards. Uses the same de-dup law as
    the score engine (snapshot diffs are canonical; events count only after the last snapshot).
    """
    from collections import defaultdict
    from datetime import timedelta

    from instilens.domain.models import PositionChange, TransactionEvent, TransactionEventFund
    from instilens.services.pipeline import _event_is_uncovered

    as_of = as_of or latest_score_date(session) or date.today()
    start = as_of - timedelta(days=window_days)
    agg: dict[int, dict] = defaultdict(lambda: {"net_flow_value": 0.0, "net_qty": 0, "inc": set(), "red": set(), "new": 0, "exited": 0})
    for c in session.scalars(
        select(PositionChange).join(Instrument, Instrument.id == PositionChange.instrument_id)
        .where(Instrument.market_code == market, PositionChange.period_end > start, PositionChange.period_end <= as_of)
    ):
        a = agg[c.instrument_id]
        a["net_flow_value"] += float(c.delta_value or 0)
        a["net_qty"] += c.delta_qty
        if c.activity in ("ADD", "NEW"):
            a["inc"].add(f"fund:{c.fund_id}")
        elif c.activity in ("REDUCE", "EXIT"):
            a["red"].add(f"fund:{c.fund_id}")
        a["new"] += c.activity == "NEW"
        a["exited"] += c.activity == "EXIT"
    latest_snap = {fid: d for fid, d in session.execute(select(PortfolioSnapshot.fund_id, func.max(PortfolioSnapshot.as_of)).group_by(PortfolioSnapshot.fund_id))}
    for e in session.scalars(
        select(TransactionEvent).where(TransactionEvent.market_code == market, TransactionEvent.is_superseded.is_(False), TransactionEvent.effective_date > start, TransactionEvent.effective_date <= as_of)
    ):
        if not _event_is_uncovered(e, latest_snap):
            continue
        a = agg[e.instrument_id]
        a["net_flow_value"] += float(event_value(session, e) or 0)
        a["net_qty"] += e.net_nominal
        party = f"fund:{e.funds[0].fund_id}" if e.confidence == "EXACT" and e.funds else f"inst:{e.institution_id}"
        (a["inc"] if e.net_nominal > 0 else a["red"]).add(party)
    _ = TransactionEventFund  # imported for relationship resolution
    rows = []
    for iid, a in agg.items():
        inst = session.get(Instrument, iid)
        rows.append({"symbol": inst.symbol, "name": inst.name, "net_flow_value": a["net_flow_value"], "net_qty": a["net_qty"],
                     "funds_increasing": len(a["inc"]), "funds_reducing": len(a["red"]), "funds_new": a["new"], "funds_exited": a["exited"]})
    return {"as_of": as_of.isoformat(), "window_days": window_days, "window_start": start.isoformat(),
            "accumulated": sorted((r for r in rows if r["net_flow_value"] > 0), key=lambda r: -r["net_flow_value"]),
            "distributed": sorted((r for r in rows if r["net_flow_value"] < 0), key=lambda r: r["net_flow_value"])}


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

    def last(stmt):
        v = session.scalar(stmt)
        return v.isoformat() if v else None

    if market == "US":
        return [
            {"source": "SEC 13F", "cadence": "Quarterly, up to 45 days after quarter end", "last": last(select(func.max(PortfolioSnapshot.as_of)).join(Fund).join(Institution).where(Institution.market_code == "US")), "delayed": True},
            {"source": "Market prices", "cadence": "Daily", "last": last(select(func.max(MarketPrice.trade_date)).join(Instrument).where(Instrument.market_code == "US")), "delayed": False},
        ]
    return [
        {"source": "KAP transaction disclosures", "cadence": "Same-day", "last": last(select(func.max(TransactionEvent.published_at)).where(TransactionEvent.market_code == "TR")), "delayed": False},
        {"source": "KAP portfolio reports", "cadence": "Monthly snapshot", "last": last(select(func.max(PortfolioSnapshot.as_of)).join(Fund).join(Institution).where(Institution.market_code == "TR")), "delayed": True},
        {"source": "Market prices", "cadence": "Daily (Yahoo, delayed)", "last": last(select(func.max(MarketPrice.trade_date)).join(Instrument).where(Instrument.market_code == "TR")), "delayed": False},
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
