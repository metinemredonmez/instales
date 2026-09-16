"""Signal detectors. Each returns a `DetectedSignal` with human-readable evidence, or None.

Thresholds are module constants on purpose: they are part of the published methodology and must
not vary per request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from itertools import pairwise

from instilens.domain.enums import ActivityType, Confidence, SignalType

ACCUMULATION_MIN_PERIODS = 3
CLUSTER_MIN_FUNDS = 3
DIVERGENCE_MIN_PRICE_MOVE_PCT = Decimal("5")
DIVERGENCE_MIN_HOLDINGS_MOVE_PCT = Decimal("10")


@dataclass(frozen=True)
class PeriodFlow:
    period_end: date
    net_qty: int
    funds_increasing: int = 0
    funds_reducing: int = 0


@dataclass(frozen=True)
class FundMove:
    fund_code: str
    activity: ActivityType
    period_end: date


@dataclass(frozen=True)
class DetectedSignal:
    signal_type: SignalType
    strength: int
    window_start: date
    window_end: date
    confidence: Confidence
    evidence: dict = field(default_factory=dict)


def _clamp(x: float) -> int:
    return int(max(0, min(100, round(x))))


def detect_accumulation(flows: list[PeriodFlow]) -> DetectedSignal | None:
    """N consecutive periods of positive (or negative) net flow at the instrument level."""
    if len(flows) < ACCUMULATION_MIN_PERIODS:
        return None
    ordered = sorted(flows, key=lambda f: f.period_end)
    tail: list[PeriodFlow] = []
    for f in reversed(ordered):
        if f.net_qty > 0 and (not tail or tail[-1].net_qty > 0):
            tail.append(f)
        elif f.net_qty < 0 and (not tail or tail[-1].net_qty < 0):
            tail.append(f)
        else:
            break
    if len(tail) < ACCUMULATION_MIN_PERIODS:
        return None
    tail.reverse()
    positive = tail[-1].net_qty > 0
    growing = sum(1 for a, b in pairwise(tail) if abs(b.net_qty) >= abs(a.net_qty))
    strength = _clamp(50 + 10 * (len(tail) - ACCUMULATION_MIN_PERIODS) + 10 * growing)
    return DetectedSignal(
        signal_type=SignalType.ACCUMULATION if positive else SignalType.DISTRIBUTION,
        strength=strength,
        window_start=tail[0].period_end,
        window_end=tail[-1].period_end,
        confidence=Confidence.INFERRED,
        evidence={
            "consecutive_periods": len(tail),
            "periods": [{"period_end": f.period_end.isoformat(), "net_qty": f.net_qty} for f in tail],
        },
    )


def detect_cluster(moves: list[FundMove], activity: ActivityType) -> DetectedSignal | None:
    """≥ CLUSTER_MIN_FUNDS distinct funds doing the same NEW/EXIT in the window."""
    if activity not in (ActivityType.NEW, ActivityType.EXIT):
        raise ValueError("cluster detection is only meaningful for NEW or EXIT")
    hits = [m for m in moves if m.activity is activity]
    funds = sorted({m.fund_code for m in hits})
    if len(funds) < CLUSTER_MIN_FUNDS:
        return None
    dates = [m.period_end for m in hits]
    return DetectedSignal(
        signal_type=(
            SignalType.NEW_POSITION_CLUSTER
            if activity is ActivityType.NEW
            else SignalType.EXIT_CLUSTER
        ),
        strength=_clamp(40 + 12 * (len(funds) - CLUSTER_MIN_FUNDS + 1)),
        window_start=min(dates),
        window_end=max(dates),
        confidence=Confidence.INFERRED,
        evidence={"funds": funds, "count": len(funds)},
    )


def detect_divergence(
    price_change_pct: Decimal,
    holdings_change_pct: Decimal,
    window_start: date,
    window_end: date,
    funds_increasing: int,
    funds_reducing: int,
) -> DetectedSignal | None:
    """Price and institutional holdings moving in opposite directions."""
    if abs(price_change_pct) < DIVERGENCE_MIN_PRICE_MOVE_PCT:
        return None
    if abs(holdings_change_pct) < DIVERGENCE_MIN_HOLDINGS_MOVE_PCT:
        return None
    if price_change_pct < 0 < holdings_change_pct:
        kind = SignalType.POSITIVE_DIVERGENCE
    elif holdings_change_pct < 0 < price_change_pct:
        kind = SignalType.NEGATIVE_DIVERGENCE
    else:
        return None
    gap = float(abs(price_change_pct) + abs(holdings_change_pct))
    return DetectedSignal(
        signal_type=kind,
        strength=_clamp(40 + gap),
        window_start=window_start,
        window_end=window_end,
        confidence=Confidence.INFERRED,
        evidence={
            "price_change_pct": str(price_change_pct),
            "holdings_change_pct": str(holdings_change_pct),
            "funds_increasing": funds_increasing,
            "funds_reducing": funds_reducing,
        },
    )
