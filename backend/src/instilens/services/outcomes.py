"""Signal outcomes: forward returns after a signal fired. Fills what prices allow; re-runs are safe.

A signal without a price on its window_end is skipped; a horizon without a price yet stays NULL and
is filled on a later run. `max_return` / `max_drawdown` are computed over the 90-day path.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.domain.models import MarketPrice, Signal, SignalOutcome

HORIZONS = {"ret_7d": 7, "ret_30d": 30, "ret_90d": 90}


def compute_outcomes(session: Session) -> int:
    updated = 0
    for sig in session.scalars(select(Signal)):
        prices = _prices_from(session, sig.instrument_id, sig.window_end, 90)
        if not prices or prices[0][0] != sig.window_end and (sig.window_end - prices[0][0]).days > 5:
            continue
        p0 = prices[0][1]
        row = session.get(SignalOutcome, sig.id) or SignalOutcome(signal_id=sig.id, price_at_signal=p0)
        row.price_at_signal = p0
        for field, days in HORIZONS.items():
            target = sig.window_end + timedelta(days=days)
            latest = [p for d, p in prices if d <= target]
            has_reached = prices[-1][0] >= target
            setattr(row, field, _ret(p0, latest[-1]) if latest and has_reached else None)
        path = [p for _, p in prices]
        row.max_return = max((_ret(p0, p) for p in path), default=None)
        row.max_drawdown = min((_ret(p0, p) for p in path), default=None)
        session.add(row)
        updated += 1
    session.flush()
    return updated


def _prices_from(session: Session, instrument_id: int, start, days: int) -> list[tuple]:
    # Latest close on/before start acts as the signal price, then every close inside the horizon.
    base = session.execute(
        select(MarketPrice.trade_date, MarketPrice.close).where(MarketPrice.instrument_id == instrument_id, MarketPrice.trade_date <= start)
        .order_by(MarketPrice.trade_date.desc()).limit(1)
    ).first()
    if base is None:
        return []
    later = session.execute(
        select(MarketPrice.trade_date, MarketPrice.close)
        .where(MarketPrice.instrument_id == instrument_id, MarketPrice.trade_date > start, MarketPrice.trade_date <= start + timedelta(days=days))
        .order_by(MarketPrice.trade_date)
    ).all()
    return [tuple(base)] + [tuple(r) for r in later]


def _ret(p0: Decimal, p1: Decimal) -> Decimal:
    return ((p1 - p0) / p0 * 100).quantize(Decimal("0.0001"))
