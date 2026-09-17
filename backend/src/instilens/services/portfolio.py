"""A user's own portfolios: holdings, the transactions behind them, and what the platform knows about each held
symbol (services/portfolio ↔ /api/v1/portfolios).

A position is either entered directly (quantity, optional average cost) or derived: once an instrument has
transactions in the portfolio, its position row is rewritten from them after every change — the quantity still
held and the FIFO average cost of the lots still held (`fifo`; a sale closes the oldest lots first, a buy's fee
enters its lot, a sale's fee reduces proceeds only) — and a direct edit of that row is refused. A position sold
down to zero disappears; its transactions stay.

The read model (`detail`) prices every position at the latest stored close (`market_prices`, so the value is as of
that close, never intraday), adds the stored scores (SMART_MONEY / CONSENSUS / CROWDING, newest per symbol), the
funds' 30-day moves on the held symbols (the same aggregation as /moves, cut to the holdings) and, for US symbols
the Form 4 job has read, the 90-day insider net value. Numbers the user typed are the only inputs; nothing derived
is stored. Money and quantities are Decimal until the JSON boundary.
"""

from __future__ import annotations

import sys
from collections import deque
from collections.abc import Iterable
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instilens.domain.enums import ScoreType, Side
from instilens.domain.models import (
    Instrument,
    MarketRow,
    Portfolio,
    PortfolioPosition,
    PortfolioTransaction,
    Score,
)
from instilens.services import insiders, plans
from instilens.services.analytics import (
    _flow_row,
    _name_parties,
    _parties,
    _window_activity,
    last_closes,
    latest_score_date,
)

MOVES_WINDOW_DAYS = 30
MAX_NAME = 64
MAX_QUANTITY = Decimal("1e12")
MAX_PRICE = Decimal("1e9")
QUANTITY_STEP = Decimal("0.0001")  # the columns' scales (Numeric(20,4) / Numeric(20,6)): a value is rounded to them before it is judged
PRICE_STEP = Decimal("0.000001")
SIDES = (Side.BUY.value, Side.SELL.value)


class PortfolioError(Exception):
    pass


class DerivedPosition(PortfolioError):
    """A hand edit of a row that transactions own (the route answers 409)."""


# ---------------------------------------------------------------- portfolios
def list_portfolios(session: Session, owner: str) -> list[dict]:
    rows = session.execute(
        select(Portfolio, func.count(PortfolioPosition.id)).outerjoin(PortfolioPosition, PortfolioPosition.portfolio_id == Portfolio.id)
        .where(Portfolio.owner_id == owner).group_by(Portfolio.id).order_by(Portfolio.id)
    ).all()
    return [_portfolio_json(p, n) for p, n in rows]


def _portfolio_json(p: Portfolio, positions: int) -> dict:
    return {"id": p.id, "name": p.name, "market": p.market_code, "currency": p.currency, "positions": positions, "created_at": p.created_at.isoformat()}


def portfolio_json(session: Session, p: Portfolio) -> dict:
    """One portfolio as the list carries it — the create / rename answers, so the client keeps one shape."""
    return _portfolio_json(p, _position_count(session, p))


def get(session: Session, owner: str, portfolio_id: int) -> Portfolio | None:
    return session.scalar(select(Portfolio).where(Portfolio.id == portfolio_id, Portfolio.owner_id == owner))


def create(session: Session, owner: str, name: str, market: str) -> Portfolio:
    name = _name(name)
    market_row = session.get(MarketRow, market)
    if market_row is None:
        raise PortfolioError(f"unknown market {market}")
    plans.enforce_limit(session, owner, "portfolios", session.scalar(select(func.count(Portfolio.id)).where(Portfolio.owner_id == owner)) or 0)
    p = Portfolio(owner_id=owner, name=name, market_code=market, currency=market_row.currency)
    session.add(p)
    session.flush()
    return p


def rename(session: Session, p: Portfolio, name: str) -> None:
    p.name = _name(name)


def delete(session: Session, p: Portfolio) -> None:
    for t in session.scalars(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id)):
        session.delete(t)
    for pos in session.scalars(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == p.id)):
        session.delete(pos)
    session.delete(p)
    session.flush()


def _name(name: str) -> str:
    name = " ".join(name.split())[:MAX_NAME]
    if len(name) < 1:
        raise PortfolioError("name is required")
    return name


def _instrument(session: Session, p: Portfolio, symbol: str) -> Instrument:
    inst = session.scalar(select(Instrument).where(Instrument.market_code == p.market_code, Instrument.symbol == symbol.strip().upper()))
    if inst is None:
        raise PortfolioError(f"unknown symbol {symbol} on {p.market_code}")
    return inst


def _amount(value, *, what: str, cap: Decimal, step: Decimal, positive: bool = True) -> Decimal:
    """A quantity or price as the body carried it (number or numeric string) — finite, rounded to the column's
    scale (`step`) first so that what is judged is what gets stored: 0.00001 lots is 0 lots, not a position that
    counts against the cap and rings alerts while holding nothing — then above zero (or not below it, for a fee)
    and at most `cap`."""
    if isinstance(value, bool):
        raise PortfolioError(f"{what} must be a number")
    try:
        d = Decimal(str(value).strip())
    except (ArithmeticError, TypeError, ValueError) as exc:
        raise PortfolioError(f"{what} must be a number") from exc
    if not d.is_finite() or abs(d) > cap:  # the cap first: a number too wide for the scale never reaches quantize
        raise PortfolioError(f"{what} must be {'above' if positive else 'at least'} 0 and at most {cap:.0f}")
    d = d.quantize(step, rounding=ROUND_HALF_UP)
    if (positive and d <= 0) or (not positive and d < 0):
        raise PortfolioError(f"{what} must be {'above' if positive else 'at least'} 0 and at most {cap:.0f}")
    return d


# ---------------------------------------------------------------- positions
def _position(session: Session, p: Portfolio, instrument_id: int) -> PortfolioPosition | None:
    return session.scalar(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == p.id, PortfolioPosition.instrument_id == instrument_id))


def _transactions(session: Session, p: Portfolio, instrument_id: int | None = None) -> list[PortfolioTransaction]:
    stmt = select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == p.id)
    if instrument_id is not None:
        stmt = stmt.where(PortfolioTransaction.instrument_id == instrument_id)
    return session.scalars(stmt.order_by(PortfolioTransaction.traded_at, PortfolioTransaction.id)).all()


def _position_count(session: Session, p: Portfolio) -> int:
    return session.scalar(select(func.count(PortfolioPosition.id)).where(PortfolioPosition.portfolio_id == p.id)) or 0


def upsert_position(session: Session, p: Portfolio, symbol: str, quantity, avg_cost=None, opened_at: date | None = None, note: str | None = None) -> PortfolioPosition:
    """Enter or change a position by hand. Refused for an instrument whose position is derived from transactions —
    add a transaction instead, so the numbers keep one source."""
    inst = _instrument(session, p, symbol)
    if _transactions(session, p, inst.id):
        raise DerivedPosition(f"{inst.symbol} is derived from its transactions; add a transaction instead")
    qty = _amount(quantity, what="quantity", cap=MAX_QUANTITY, step=QUANTITY_STEP)
    cost = _amount(avg_cost, what="avg_cost", cap=MAX_PRICE, step=PRICE_STEP) if avg_cost is not None else None
    pos = _position(session, p, inst.id)
    if pos is None:
        plans.enforce_limit(session, p.owner_id, "portfolio_positions", _position_count(session, p))
        pos = PortfolioPosition(portfolio_id=p.id, instrument_id=inst.id, quantity=qty)
        session.add(pos)
    pos.quantity, pos.avg_cost, pos.opened_at, pos.note = qty, cost, opened_at, (note or "").strip()[:256] or None
    session.flush()
    return pos


def delete_position(session: Session, p: Portfolio, position_id: int) -> bool:
    """Drop a position; its transactions (if any) go with it, so it does not reappear at the next rederive."""
    pos = session.scalar(select(PortfolioPosition).where(PortfolioPosition.id == position_id, PortfolioPosition.portfolio_id == p.id))
    if pos is None:
        return False
    for t in _transactions(session, p, pos.instrument_id):
        session.delete(t)
    session.delete(pos)
    session.flush()
    return True


# ---------------------------------------------------------------- transactions
def fifo(transactions: Iterable[PortfolioTransaction]) -> tuple[Decimal, Decimal | None, date | None]:
    """(quantity held, average cost of the lots still held, date the open position was opened) from a run of
    transactions in trade order. A buy opens a lot at (quantity × price + fee) / quantity; a sale closes lots from
    the oldest, its fee touching proceeds only; the average cost is the held lots' cost over their quantity, so it
    moves with a buy and stays put through a sale. PortfolioError when a sale exceeds what is held on its date.
    Same-day rows run in the order they were recorded; a row not yet written (no id — the one being checked before
    it lands) runs last on its day, so a sale recorded after the same day's buy closes it."""
    lots: deque[list] = deque()  # [quantity, unit cost, opened on]
    for t in sorted(transactions, key=lambda t: (t.traded_at, t.id if t.id is not None else sys.maxsize)):
        qty = Decimal(t.quantity)
        if t.side == Side.BUY.value:
            lots.append([qty, (qty * Decimal(t.price) + Decimal(t.fee or 0)) / qty, t.traded_at])
            continue
        remaining = qty
        while remaining > 0:
            if not lots:
                raise PortfolioError(f"sale of {qty.normalize()} on {t.traded_at} exceeds the quantity held")
            take = min(lots[0][0], remaining)
            lots[0][0] -= take
            remaining -= take
            if lots[0][0] == 0:
                lots.popleft()
    held = sum((lot[0] for lot in lots), Decimal(0))
    if held == 0:
        return Decimal(0), None, None
    return held, sum((lot[0] * lot[1] for lot in lots), Decimal(0)) / held, lots[0][2]


def _rederive(session: Session, p: Portfolio, instrument_id: int) -> PortfolioPosition | None:
    """Rewrite the instrument's position row from its transactions (gone when nothing is held or recorded)."""
    txs = _transactions(session, p, instrument_id)
    pos = _position(session, p, instrument_id)
    held, avg, opened = fifo(txs) if txs else (Decimal(0), None, None)
    if held == 0:
        if pos is not None:
            session.delete(pos)
            session.flush()
        return None
    if pos is None:
        pos = PortfolioPosition(portfolio_id=p.id, instrument_id=instrument_id, quantity=held)
        session.add(pos)
    pos.quantity, pos.avg_cost, pos.opened_at = held, avg, opened
    session.flush()
    return pos


def add_transaction(session: Session, p: Portfolio, symbol: str, side: str, quantity, price, traded_at: date, fee=None, note: str | None = None) -> PortfolioTransaction:
    """Record a buy or sell and rederive the position. A sale is checked against the FIFO run *with* the new row
    before anything is written, so an oversell never lands."""
    inst = _instrument(session, p, symbol)
    side = (side or "").strip().upper()
    if side not in SIDES:
        raise PortfolioError("side must be BUY or SELL")
    if traded_at > date.today():
        raise PortfolioError("traded_at cannot be in the future")
    row = PortfolioTransaction(
        portfolio_id=p.id, instrument_id=inst.id, side=side,
        quantity=_amount(quantity, what="quantity", cap=MAX_QUANTITY, step=QUANTITY_STEP), price=_amount(price, what="price", cap=MAX_PRICE, step=PRICE_STEP),
        traded_at=traded_at, fee=_amount(fee, what="fee", cap=MAX_PRICE, step=QUANTITY_STEP, positive=False) if fee is not None else None, note=(note or "").strip()[:256] or None,
    )
    existing = _transactions(session, p, inst.id)
    fifo([*existing, row])  # raises on an oversell
    if not existing and _position(session, p, inst.id) is None:
        plans.enforce_limit(session, p.owner_id, "portfolio_positions", _position_count(session, p))
    session.add(row)
    session.flush()
    _rederive(session, p, inst.id)
    return row


def delete_transaction(session: Session, p: Portfolio, transaction_id: int) -> bool:
    """Remove a transaction and rederive; refused when the remaining run would oversell (delete the sale first)."""
    row = session.scalar(select(PortfolioTransaction).where(PortfolioTransaction.id == transaction_id, PortfolioTransaction.portfolio_id == p.id))
    if row is None:
        return False
    fifo([t for t in _transactions(session, p, row.instrument_id) if t.id != row.id])
    session.delete(row)
    session.flush()
    _rederive(session, p, row.instrument_id)
    return True


def transaction_json(t: PortfolioTransaction, symbol: str) -> dict:
    """One transaction row as the detail's `transactions` list (and the POST answer) carries it."""
    return {"id": t.id, "symbol": symbol, "side": t.side, "quantity": float(t.quantity), "price": float(t.price), "traded_at": t.traded_at.isoformat(),
            "fee": float(t.fee) if t.fee is not None else None, "note": t.note, "created_at": t.created_at.isoformat()}


# ---------------------------------------------------------------- the read model
def _f(v: Decimal | None, places: int = 4) -> float | None:
    return round(float(v), places) if v is not None else None


def _latest_scores(session: Session, instrument_ids: list[int]) -> dict[tuple[int, str], Score]:
    """Newest instrument-level row per (instrument, type) — one query, the latest date wins."""
    out: dict[tuple[int, str], Score] = {}
    if not instrument_ids:
        return out
    rows = session.scalars(
        select(Score).where(Score.instrument_id.in_(instrument_ids), Score.fund_id.is_(None),
                            Score.score_type.in_([ScoreType.SMART_MONEY, ScoreType.CONSENSUS, ScoreType.CROWDING]))
        .order_by(Score.as_of)
    )
    for s in rows:
        out[(s.instrument_id, s.score_type)] = s
    return out


def detail(session: Session, p: Portfolio) -> dict:
    """The GET /portfolios/{id} payload: positions priced at the latest close, weights and P&L over the priced
    ones, the stored scores, the funds' 30-day counts on each symbol, and the 30-day moves list."""
    positions = session.scalars(select(PortfolioPosition).where(PortfolioPosition.portfolio_id == p.id).order_by(PortfolioPosition.id)).all()
    txs = _transactions(session, p)
    ids = [pos.instrument_id for pos in positions]
    derived = {t.instrument_id for t in txs}
    wanted = set(ids) | derived  # a fully sold symbol still names its transactions
    instruments = {i.id: i for i in session.scalars(select(Instrument).where(Instrument.id.in_(wanted)))} if wanted else {}
    scores = _latest_scores(session, ids)
    as_of = latest_score_date(session) or date.today()
    window_start = as_of - timedelta(days=MOVES_WINDOW_DAYS)
    activity = _window_activity(session, p.market_code, window_start, as_of, instrument_ids=ids)
    flows = {iid: _flow_row(a) for iid, a in activity.items()}
    today = date.today()

    rows, market_total, cost_total, priced_cost = [], Decimal(0), Decimal(0), Decimal(0)
    for pos in positions:
        inst = instruments[pos.instrument_id]
        closes = last_closes(session, inst.id, today, 1)
        close_date, last_close = closes[0] if closes else (None, None)
        market_value = pos.quantity * last_close if last_close is not None else None
        cost_value = pos.quantity * pos.avg_cost if pos.avg_cost is not None else None
        pnl = market_value - cost_value if market_value is not None and cost_value is not None else None
        if market_value is not None:
            market_total += market_value
        if cost_value is not None:
            cost_total += cost_value
        if pnl is not None:
            priced_cost += cost_value  # the P&L total is over positions that have both a cost and a close
        sm, cs, cr = scores.get((inst.id, ScoreType.SMART_MONEY)), scores.get((inst.id, ScoreType.CONSENSUS)), scores.get((inst.id, ScoreType.CROWDING))
        flow = flows.get(inst.id)
        insider = insiders.detail(session, inst)
        rows.append({
            "id": pos.id, "symbol": inst.symbol, "name": inst.name, "derived": inst.id in derived,
            "quantity": _f(pos.quantity), "avg_cost": _f(pos.avg_cost, 6), "opened_at": pos.opened_at.isoformat() if pos.opened_at else None, "note": pos.note,
            "last_close": _f(last_close), "close_date": close_date.isoformat() if close_date else None,
            "market_value": _f(market_value, 2), "cost_value": _f(cost_value, 2), "pnl_value": _f(pnl, 2),
            "pnl_pct": _f(pnl / cost_value * 100, 2) if pnl is not None and cost_value else None,
            "weight_pct": None,  # filled below, once the total is known
            "smart_money_score": float(sm.adjusted_score) if sm else None,
            "consensus_score": float(cs.adjusted_score) if cs else None,
            "crowding_score": float(cr.adjusted_score) if cr else None,
            "funds_increasing_30d": flow["funds_increasing"] if flow else 0,
            "funds_reducing_30d": flow["funds_reducing"] if flow else 0,
            "insiders_net_90d": insider["net_value"] if insider else None,  # US symbols the Form 4 job has read; null elsewhere
            "_market_value": market_value,
        })
    for r in rows:
        mv = r.pop("_market_value")
        r["weight_pct"] = _f(mv / market_total * 100, 2) if mv is not None and market_total else None
    pnl_total = sum((Decimal(str(r["pnl_value"])) for r in rows if r["pnl_value"] is not None), Decimal(0)) if priced_cost else None
    moves = []
    for iid, a in activity.items():
        row = flows[iid]
        parties = sorted(_parties(a), key=lambda x: (-abs(x["delta_value"] or 0), x["kind"], x["ref"]))
        if not parties:
            continue
        moves.append({**row, "party_count": len(parties), "parties": parties[:5]})  # as /moves: at most five named, largest first
    moves.sort(key=lambda r: (-abs(r["net_flow_value"]), r["symbol"]))
    _name_parties(session, moves)
    return {
        "portfolio": _portfolio_json(p, len(positions)),
        "as_of": as_of.isoformat(),
        "positions": rows,
        "totals": {
            "market_value": _f(market_total, 2) if any(r["market_value"] is not None for r in rows) else None,
            "cost_value": _f(cost_total, 2) if any(r["cost_value"] is not None for r in rows) else None,
            "pnl_value": _f(pnl_total, 2) if pnl_total is not None else None,
            "pnl_pct": _f(pnl_total / priced_cost * 100, 2) if pnl_total is not None else None,
            "unpriced": sum(1 for r in rows if r["market_value"] is None),  # positions without a stored close (not in market_value)
        },
        "moves": {"window_days": MOVES_WINDOW_DAYS, "window_start": window_start.isoformat(), "rows": moves},
        "transactions": [transaction_json(t, instruments[t.instrument_id].symbol) for t in reversed(txs)],
    }


# ---------------------------------------------------------------- alerts (services/alerts.PORTFOLIO_MOVE)
def held_instruments(session: Session) -> list[tuple[str, int]]:
    """(owner_id, instrument_id) pairs across every portfolio, each once — the implicit PORTFOLIO_MOVE rules."""
    return session.execute(
        select(Portfolio.owner_id, PortfolioPosition.instrument_id).join(PortfolioPosition, PortfolioPosition.portfolio_id == Portfolio.id).distinct()
        .order_by(Portfolio.owner_id, PortfolioPosition.instrument_id)
    ).all()
