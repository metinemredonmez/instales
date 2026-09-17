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
    # v2: what the position was worth on each side (reported value, else qty × close), how much of the
    # book it is, and the quantity change relative to the starting position.
    from_value: Decimal | None = None
    to_value: Decimal | None = None
    delta_weight_pct: Decimal | None = None
    pct_change_qty: Decimal | None = None
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
    from_prices: dict[str, Decimal] | None = None,
) -> list[PositionDelta]:
    """Diff two snapshots of the same fund.

    With no previous snapshot every holding is NEW (first observation) — callers should treat
    the very first snapshot of a fund as a baseline, not as buying activity (see pipeline).
    `prices` (symbol → close at period_end) lets us value the delta when the report has no value;
    `from_prices` (closes at period_start) values the previous side the same way, so `from_value`
    means the same thing whether the report carried a value or not; None falls back to `prices`.
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
        from_weight = p.weight_pct if p else None
        to_weight = c.weight_pct if c else None
        deltas.append(
            PositionDelta(
                fund_code=current.fund_code,
                instrument_symbol=symbol,
                period_start=previous.as_of if previous else None,
                period_end=current.as_of,
                from_qty=from_qty,
                to_qty=to_qty,
                delta_qty=delta_qty,
                from_weight_pct=from_weight,
                to_weight_pct=to_weight,
                delta_value=_value_delta(delta_qty, symbol, p, c, prices),
                activity=classify(from_qty, to_qty),
                from_value=_holding_value(p, symbol, prices if from_prices is None else from_prices),
                to_value=_holding_value(c, symbol, prices),
                delta_weight_pct=to_weight - from_weight if from_weight is not None and to_weight is not None else None,
                pct_change_qty=(Decimal(delta_qty) / Decimal(from_qty) * 100).quantize(Decimal("0.0001")) if from_qty else None,
            )
        )
    return deltas


def _holding_value(h: HoldingView | None, symbol: str, prices: dict[str, Decimal] | None) -> Decimal | None:
    """What the position was worth: the report's own figure when it has one, else quantity × close.
    A side the fund does not hold is worth 0 (NEW starts from 0, EXIT ends at 0)."""
    if h is None:
        return Decimal(0)
    if h.market_value is not None:
        return h.market_value
    if prices and symbol in prices:
        return Decimal(h.quantity) * prices[symbol]
    return None


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
