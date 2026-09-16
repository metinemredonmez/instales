"""Position reconstruction: two consecutive snapshots of one fund → per-instrument deltas.

Pure functions; persistence lives in services/pipeline.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from instilens.domain.enums import ActivityType, Confidence


@dataclass(frozen=True)
class HoldingView:
    instrument_symbol: str
    quantity: int
    market_value: Decimal | None = None
    weight_pct: Decimal | None = None


@dataclass(frozen=True)
class SnapshotView:
    fund_code: str
    as_of: date
    holdings: tuple[HoldingView, ...]

    def by_symbol(self) -> dict[str, HoldingView]:
        return {h.instrument_symbol: h for h in self.holdings}


@dataclass(frozen=True)
class PositionDelta:
    fund_code: str
    instrument_symbol: str
    period_start: date | None
    period_end: date
    from_qty: int
    to_qty: int
    delta_qty: int
    from_weight_pct: Decimal | None
    to_weight_pct: Decimal | None
    delta_value: Decimal | None
    activity: ActivityType
    confidence: Confidence = Confidence.INFERRED


def classify(from_qty: int, to_qty: int) -> ActivityType:
    if from_qty == 0 and to_qty > 0:
        return ActivityType.NEW
    if from_qty > 0 and to_qty == 0:
        return ActivityType.EXIT
    if to_qty > from_qty:
        return ActivityType.ADD
    if to_qty < from_qty:
        return ActivityType.REDUCE
    return ActivityType.HOLD


def diff_snapshots(
    previous: SnapshotView | None,
    current: SnapshotView,
    prices: dict[str, Decimal] | None = None,
) -> list[PositionDelta]:
    """Diff two snapshots of the same fund.

    With no previous snapshot every holding is NEW (first observation) — callers should treat
    the very first snapshot of a fund as a baseline, not as buying activity (see pipeline).
    `prices` (symbol → close at period_end) lets us value the delta when the report has no value.
    """
    if previous is not None and previous.fund_code != current.fund_code:
        raise ValueError("snapshots belong to different funds")
    prev = previous.by_symbol() if previous else {}
    curr = current.by_symbol()
    deltas: list[PositionDelta] = []
    for symbol in sorted(prev.keys() | curr.keys()):
        p, c = prev.get(symbol), curr.get(symbol)
        from_qty = p.quantity if p else 0
        to_qty = c.quantity if c else 0
        delta_qty = to_qty - from_qty
        if delta_qty == 0 and to_qty == 0:
            continue  # absent on both sides (can't happen) or zero-zero
        deltas.append(
            PositionDelta(
                fund_code=current.fund_code,
                instrument_symbol=symbol,
                period_start=previous.as_of if previous else None,
                period_end=current.as_of,
                from_qty=from_qty,
                to_qty=to_qty,
                delta_qty=delta_qty,
                from_weight_pct=p.weight_pct if p else None,
                to_weight_pct=c.weight_pct if c else None,
                delta_value=_value_delta(delta_qty, symbol, p, c, prices),
                activity=classify(from_qty, to_qty),
            )
        )
    return deltas


def _value_delta(
    delta_qty: int,
    symbol: str,
    p: HoldingView | None,
    c: HoldingView | None,
    prices: dict[str, Decimal] | None,
) -> Decimal | None:
    # Prefer a real close price at period end: value of the *quantity change*, not the mark-to-
    # market change (which would mix price moves into flow).
    if prices and symbol in prices:
        return Decimal(delta_qty) * prices[symbol]
    if c and c.market_value is not None and c.quantity:
        return Decimal(delta_qty) * (c.market_value / Decimal(c.quantity))
    if p and p.market_value is not None and p.quantity:
        return Decimal(delta_qty) * (p.market_value / Decimal(p.quantity))
    return None
