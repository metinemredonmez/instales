"""Who holds a stock and how the holding is spread; how far two or more funds' books overlap.

Read models over the funds' LATEST portfolio snapshots: `/stocks/{symbol}/ownership`, `/funds/overlap`, the CROWDING
score `pipeline.compute_intelligence` writes, and the ownership AI tools. One row per fund — its newest snapshot on
or before the reference date, never two dates of the same fund — so every total adds each fund exactly once. A fund
whose newest report is older than STALE_PERIODS reporting periods (TR 60 days, US 182) is left out of the holders
and counted in `stale_holders`: its book may have changed since. Every number is a `snapshot_holdings` row as the
fund reported it (quantity, market_value, weight_pct) or one documented step away from those rows (pct_of_shares,
the top-10 share, HHI, the overlap figures); nothing is ever distributed across funds.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from instilens.domain.enums import ScoreType
from instilens.domain.models import (
    Fund,
    Institution,
    Instrument,
    MarketRow,
    PortfolioSnapshot,
    PositionChange,
    Score,
    SnapshotHolding,
)
from instilens.engine import crowding

# A holder whose newest report is more than this many reporting periods old (pipeline.PERIOD_DAYS: TR 30, US 91) is
# not counted — the score window would otherwise carry books nobody has confirmed for months.
STALE_PERIODS = 2
TOP_HOLDERS = 10  # the "top-10 share" of the held quantity
MAX_OVERLAP_FUNDS = 6


def staleness_days(market: str) -> int:
    from instilens.services.pipeline import PERIOD_DAYS

    return STALE_PERIODS * PERIOD_DAYS.get(market, 30)


def reference_date(session: Session) -> date:
    """The day the read models describe: the latest compute day (the CROWDING rows and the holders then agree),
    today before the first compute."""
    from instilens.services.analytics import latest_score_date

    return latest_score_date(session) or date.today()


# --------------------------------------------------------------------------- holders


@dataclass(frozen=True)
class Holder:
    """One fund's position from its latest snapshot, with the move that produced it (the position change ending at
    that snapshot — null when the snapshot is the fund's first) and the snapshot's confidence."""

    fund_id: int
    fund: str
    name: str
    institution_id: int
    institution: str
    quantity: int
    market_value: Decimal | None
    weight_pct: Decimal | None
    as_of: date
    confidence: str
    last_move: str | None
    last_move_period_end: date | None


@dataclass(frozen=True)
class Holders:
    fresh: list[Holder]  # within the staleness window, largest quantity first
    stale: int  # funds whose newest report holds the stock but is too old to count


def _holder_rows(session: Session, market: str, on: date, instrument_id: int | None = None):
    """(instrument_id, Holder) for every holding in the funds' newest snapshots on or before `on`, largest quantity
    first — the whole market in one query, or one instrument. A row restated at quantity 0 (a 13F line without a
    share count, a report that lists the position as closed) is not a holder: the positions engine reads it as an
    EXIT. `last_move` comes from the position change whose `to_snapshot_id` is that snapshot: the fund's newest
    change for the instrument (a newer one would need a newer snapshot)."""
    latest = (
        select(PortfolioSnapshot.fund_id.label("fund_id"), func.max(PortfolioSnapshot.as_of).label("as_of"))
        .join(Fund, Fund.id == PortfolioSnapshot.fund_id)
        .join(Institution, Institution.id == Fund.institution_id)
        .where(Institution.market_code == market, PortfolioSnapshot.as_of <= on)
        .group_by(PortfolioSnapshot.fund_id)
        .subquery()
    )
    stmt = (
        select(SnapshotHolding, PortfolioSnapshot.as_of, PortfolioSnapshot.confidence, Fund, Institution.name,
               PositionChange.activity, PositionChange.period_end)
        .join(PortfolioSnapshot, PortfolioSnapshot.id == SnapshotHolding.snapshot_id)
        .join(latest, (latest.c.fund_id == PortfolioSnapshot.fund_id) & (latest.c.as_of == PortfolioSnapshot.as_of))
        .join(Fund, Fund.id == PortfolioSnapshot.fund_id)
        .join(Institution, Institution.id == Fund.institution_id)
        .outerjoin(PositionChange, (PositionChange.to_snapshot_id == PortfolioSnapshot.id) & (PositionChange.instrument_id == SnapshotHolding.instrument_id))
        .where(SnapshotHolding.quantity > 0)
        .order_by(SnapshotHolding.quantity.desc(), Fund.code)
    )
    if instrument_id is not None:
        stmt = stmt.where(SnapshotHolding.instrument_id == instrument_id)
    for h, as_of, confidence, fund, institution_name, activity, period_end in session.execute(stmt):
        yield h.instrument_id, Holder(
            fund_id=fund.id, fund=fund.code, name=fund.name, institution_id=fund.institution_id, institution=institution_name,
            quantity=h.quantity, market_value=h.market_value, weight_pct=h.weight_pct, as_of=as_of, confidence=confidence,
            last_move=activity, last_move_period_end=period_end,
        )


def _split(rows, market: str, on: date) -> dict[int, Holders]:
    cutoff = on - timedelta(days=staleness_days(market))
    fresh: dict[int, list[Holder]] = defaultdict(list)
    stale: dict[int, int] = defaultdict(int)
    for instrument_id, holder in rows:
        if holder.as_of >= cutoff:
            fresh[instrument_id].append(holder)
        else:
            stale[instrument_id] += 1
    return {iid: Holders(fresh=fresh.get(iid, []), stale=stale.get(iid, 0)) for iid in set(fresh) | set(stale)}


def latest_holders(session: Session, instrument_id: int, market: str, on: date | None = None) -> Holders:
    """The funds holding one instrument as of `on` (default: the reference date), one row per fund."""
    on = on or reference_date(session)
    return _split(_holder_rows(session, market, on, instrument_id), market, on).get(instrument_id, Holders([], 0))


def market_holders(session: Session, market: str, on: date) -> dict[int, Holders]:
    """Every instrument's holders in the market as of `on`, from one query — what the pipeline scores from."""
    return _split(_holder_rows(session, market, on), market, on)


# --------------------------------------------------------------------------- totals and concentration


def concentration(quantities: Iterable[int]) -> tuple[Decimal | None, Decimal | None]:
    """(share of the held quantity in the TOP_HOLDERS largest holders in %, HHI = Σ (holder share in %)², 0..10000).
    Both None when nothing is held."""
    q = sorted((int(x) for x in quantities if x > 0), reverse=True)
    total = sum(q)
    if total == 0:
        return None, None
    shares = [Decimal(x) / total * 100 for x in q]
    return sum(shares[:TOP_HOLDERS], Decimal(0)), sum((s * s for s in shares), Decimal(0))


def pct_of_shares(quantity: int, shares_outstanding: int | None) -> Decimal | None:
    """quantity / shares outstanding × 100, None while the share count is unknown (the fundamentals job fills it)."""
    if not shares_outstanding or shares_outstanding <= 0:
        return None
    return Decimal(quantity) / Decimal(shares_outstanding) * 100


def crowding_inputs(held: Holders, shares_outstanding: int | None, funds_increasing: int, funds_reducing: int, window_days: int) -> crowding.CrowdingInputs:
    """The crowding engine's inputs from a holder set: counts and concentration from the holder rows, the held share
    of the company from `instruments.shares_outstanding`, breadth momentum from the score window's activity."""
    top10, hhi = concentration(h.quantity for h in held.fresh)
    held_pct = pct_of_shares(sum(h.quantity for h in held.fresh), shares_outstanding)
    return crowding.CrowdingInputs(
        holders=len(held.fresh),
        pct_of_shares=_f(held_pct, 4),
        top10_pct_of_held=_f(top10),
        hhi=_f(hhi),
        funds_increasing=funds_increasing,
        funds_reducing=funds_reducing,
        window_days=window_days,
        stale_holders=held.stale,
    )


def _f(v: Decimal | None, places: int = 2) -> float | None:
    return round(float(v), places) if v is not None else None


# --------------------------------------------------------------------------- /stocks/{symbol}/ownership


def stock_ownership(session: Session, market: str, symbol: str, limit: int = 50) -> dict | None:
    """The contract of `/stocks/{symbol}/ownership`: totals over every fresh holder, the `limit` largest holders,
    the stored CROWDING score of the reference date (null until the pipeline has written one). None = unknown symbol."""
    instrument = session.scalar(select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol.upper()))
    if instrument is None:
        return None
    on = reference_date(session)
    held = latest_holders(session, instrument.id, market, on)
    holders = held.fresh
    shares = instrument.shares_outstanding if instrument.shares_outstanding else None
    quantity = sum(h.quantity for h in holders)
    top10, hhi = concentration(h.quantity for h in holders)
    valued = [h.market_value for h in holders if h.market_value is not None]
    return {
        "symbol": instrument.symbol,
        "name": instrument.name,
        "market": instrument.market_code,
        "as_of": max(h.as_of for h in holders).isoformat() if holders else None,
        "shares_outstanding": shares,
        "shares_as_of": instrument.shares_as_of.isoformat() if shares and instrument.shares_as_of else None,
        "currency": session.get(MarketRow, market).currency,
        "totals": {
            "holders": len(holders),
            "institutions": len({h.institution_id for h in holders}),
            "quantity": quantity,
            "market_value": float(sum(valued, Decimal(0))) if valued else None,  # Σ of the values the reports state
            "unvalued_holders": len(holders) - len(valued),  # holders whose report states no value (not in market_value)
            "pct_of_shares": _f(pct_of_shares(quantity, shares), 4),
            "top10_pct_of_held": _f(top10),
            "hhi": _f(hhi),
        },
        "crowding": _stored_crowding(session, instrument.id, on),
        "holders": [_holder_json(h, shares) for h in holders[:limit]],
        "stale_holders": held.stale,
    }


def _holder_json(h: Holder, shares: int | None) -> dict:
    return {
        "fund": h.fund,
        "name": h.name,
        "institution": h.institution,
        "quantity": h.quantity,
        "market_value": float(h.market_value) if h.market_value is not None else None,
        "weight_pct": float(h.weight_pct) if h.weight_pct is not None else None,
        "pct_of_shares": _f(pct_of_shares(h.quantity, shares), 4),
        "as_of": h.as_of.isoformat(),
        "last_move": h.last_move,
        "last_move_period_end": h.last_move_period_end.isoformat() if h.last_move_period_end else None,
        "confidence": h.confidence,
    }


def _stored_crowding(session: Session, instrument_id: int, on: date) -> dict | None:
    """The CROWDING row of the reference date, in the contract's shape (score, level, why). Read, never recomputed
    here, so the stock page (header chip, `stock_detail.scores.CROWDING`), the ownership AI tools and this page show
    one number."""
    row = session.scalar(
        select(Score).where(Score.instrument_id == instrument_id, Score.score_type == ScoreType.CROWDING, Score.fund_id.is_(None), Score.as_of == on)
    )
    if row is None:
        return None
    score = float(row.adjusted_score)
    return {"score": score, "level": row.components.get("level") or crowding.level_of(score), "why": row.components.get("why", {})}


# --------------------------------------------------------------------------- fund books and overlap


@dataclass(frozen=True)
class Position:
    symbol: str
    name: str
    quantity: int
    weight_pct: Decimal | None


@dataclass(frozen=True)
class Book:
    as_of: date | None  # None when the fund has no snapshot yet
    holdings: dict[str, Position]  # by symbol


def fund_books(session: Session, funds: list[Fund]) -> dict[str, Book]:
    """Each fund's latest snapshot as a book keyed by symbol (any age — the fund's `as_of` says how old); two
    queries for any number of funds. A position restated at quantity 0 is not in the book (see `_holder_rows`)."""
    latest = (
        select(PortfolioSnapshot.fund_id.label("fund_id"), func.max(PortfolioSnapshot.as_of).label("as_of"))
        .where(PortfolioSnapshot.fund_id.in_([f.id for f in funds])).group_by(PortfolioSnapshot.fund_id).subquery()
    )
    snaps = session.scalars(
        select(PortfolioSnapshot).options(selectinload(PortfolioSnapshot.holdings))
        .join(latest, (latest.c.fund_id == PortfolioSnapshot.fund_id) & (latest.c.as_of == PortfolioSnapshot.as_of))
    ).all()
    instrument_ids = {h.instrument_id for s in snaps for h in s.holdings}
    names = {i.id: (i.symbol, i.name) for i in session.scalars(select(Instrument).where(Instrument.id.in_(instrument_ids)))} if instrument_ids else {}
    by_fund: dict[int, Book] = {}
    for s in snaps:
        holdings = {}
        for h in s.holdings:
            if h.quantity <= 0:
                continue
            symbol, name = names[h.instrument_id]
            holdings[symbol] = Position(symbol=symbol, name=name, quantity=h.quantity, weight_pct=h.weight_pct)
        by_fund[s.fund_id] = Book(as_of=s.as_of, holdings=holdings)
    return {f.code: by_fund.get(f.id, Book(None, {})) for f in funds}


def symbol_overlap(a: Iterable[str], b: Iterable[str]) -> float:
    """Symbols both hold as a share of the symbols either holds (Jaccard, %), one decimal; 0 when both are empty."""
    a, b = set(a), set(b)
    return round(100 * len(a & b) / max(len(a | b), 1), 1)


def weighted_overlap(a: Mapping[str, Decimal | None], b: Mapping[str, Decimal | None]) -> Decimal | None:
    """Σ min(w_a, w_b) over the symbols both hold, in percentage points of a book — how much of either portfolio the
    other one mirrors. None when either fund reports no weight for one of the common holdings: a partial sum would
    read as a smaller overlap than the truth."""
    common = a.keys() & b.keys()
    if any(a[s] is None or b[s] is None for s in common):
        return None
    return sum((min(a[s], b[s]) for s in common), Decimal(0))


class OverlapRequestError(ValueError):
    """The request cannot be answered as asked — fewer than two codes, more than MAX_OVERLAP_FUNDS, funds of two
    markets, or a fund outside the caller's market (the route answers 422). An unknown code is a 404, signalled by
    `fund_overlap` returning None."""


def fund_overlap(session: Session, codes: Iterable[str], market: str | None = None) -> dict | None:
    """The contract of `/funds/overlap`: every pair's symbol and weighted overlap, and the symbols every requested
    fund holds with each fund's weight. Codes are de-duplicated in request order. `market` pins the answer to one
    market (the AI assistant's); the route leaves it None and only requires that the funds share a market."""
    wanted = list(dict.fromkeys(c.strip().upper() for c in codes if c and c.strip()))
    if len(wanted) < 2:
        raise OverlapRequestError("at least two fund codes are needed")
    if len(wanted) > MAX_OVERLAP_FUNDS:
        raise OverlapRequestError(f"at most {MAX_OVERLAP_FUNDS} fund codes per request")
    funds = {f.code: f for f in session.scalars(select(Fund).options(selectinload(Fund.institution)).where(Fund.code.in_(wanted)))}
    if any(c not in funds for c in wanted):
        return None
    markets = {funds[c].institution.market_code for c in wanted}
    if len(markets) > 1:
        raise OverlapRequestError("all funds must be in one market")
    if market is not None and markets != {market}:
        raise OverlapRequestError(f"all funds must be in the {market} market")
    books = fund_books(session, [funds[c] for c in wanted])
    weights = {c: {s: p.weight_pct for s, p in books[c].holdings.items()} for c in wanted}
    pairwise = []
    for i, a in enumerate(wanted):
        for b in wanted[i + 1:]:
            weighted = weighted_overlap(weights[a], weights[b])
            pairwise.append({"a": a, "b": b, "overlap_pct_symbols": symbol_overlap(books[a].holdings, books[b].holdings),
                             "overlap_pct_weighted": _f(weighted)})
    common = set.intersection(*(set(books[c].holdings) for c in wanted))
    common_all = [
        {"symbol": s, "name": books[wanted[0]].holdings[s].name, "weights": {c: _f(weights[c][s], 4) for c in wanted}}
        for s in common
    ]
    # Sorted by the smallest weight any fund gives the symbol, largest first; a symbol some fund holds without a
    # reported weight goes last (its smallest weight is unknown), then alphabetical.
    common_all.sort(key=lambda r: (any(w is None for w in r["weights"].values()), -min((w for w in r["weights"].values() if w is not None), default=0), r["symbol"]))
    dates = [books[c].as_of for c in wanted if books[c].as_of is not None]
    return {
        "as_of": max(dates).isoformat() if dates else None,
        "funds": [{"code": c, "name": funds[c].name, "institution": funds[c].institution.name,
                   "as_of": books[c].as_of.isoformat() if books[c].as_of else None, "holdings": len(books[c].holdings)} for c in wanted],
        "pairwise": pairwise,
        "common_all": common_all,
    }
