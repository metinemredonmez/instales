"""Deterministic, explainable scores. No ML, no LLM.

Smart Money Score (instrument):
    30% breadth · 25% net flow · 15% persistence · 10% new positions · 10% conviction · 10% freshness
    then × confidence multiplier (flow-weighted, see CONFIDENCE_MULTIPLIER).
Consensus Score (instrument): direction agreement across funds, size-weighted.
Conviction Score (fund × instrument): how much the position grew *relative to the fund's own book*.

Every function returns a breakdown so the UI can answer "Why 87?".
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal

from instilens.domain.enums import CONFIDENCE_MULTIPLIER, Confidence

WEIGHTS = {
    "breadth": 0.30,
    "net_flow": 0.25,
    "persistence": 0.15,
    "new_positions": 0.10,
    "conviction": 0.10,
    "freshness": 0.10,
}

# Saturation points: a value at/above this earns full marks for that component.
BREADTH_SATURATION = 12  # funds increasing
FLOW_SATURATION_TRY = Decimal("500_000_000")  # ₺500M net flow
PERSISTENCE_SATURATION = 4  # consecutive periods
NEW_POSITIONS_SATURATION = 4
FRESHNESS_HALF_LIFE_DAYS = 14


@dataclass(frozen=True)
class InstrumentActivity:
    funds_increasing: int
    funds_reducing: int
    funds_unchanged: int
    funds_new: int
    funds_exited: int
    net_flow_value: Decimal  # signed, in market currency
    persistence_periods: int  # consecutive periods with same-sign net flow
    avg_conviction: float  # 0..1, mean conviction of increasing funds
    days_since_last_activity: int
    flow_by_confidence: dict[Confidence, Decimal] = field(default_factory=dict)

    @property
    def funds_active(self) -> int:
        return self.funds_increasing + self.funds_reducing + self.funds_unchanged


@dataclass(frozen=True)
class ScoreBreakdown:
    raw: float
    adjusted: float
    components: dict[str, float]
    confidence_multiplier: float

    def as_json(self) -> dict:
        return {
            "raw": round(self.raw, 2),
            "adjusted": round(self.adjusted, 2),
            "confidence_multiplier": round(self.confidence_multiplier, 3),
            "weights": WEIGHTS,
            "components": {k: round(v, 4) for k, v in self.components.items()},
        }


def _sat(value: float, saturation: float) -> float:
    """Concave saturation in [0, 1]; halfway to saturation gives ~0.7, not 0.5."""
    if value <= 0:
        return 0.0
    return min(1.0, math.sqrt(value / saturation))


def confidence_multiplier(flow_by_confidence: dict[Confidence, Decimal]) -> float:
    total = sum((abs(v) for v in flow_by_confidence.values()), Decimal(0))
    if total == 0:
        return CONFIDENCE_MULTIPLIER[Confidence.INFERRED]
    weighted = sum(
        float(abs(v) / total) * CONFIDENCE_MULTIPLIER[c] for c, v in flow_by_confidence.items()
    )
    return round(weighted, 4)


def smart_money_score(a: InstrumentActivity) -> ScoreBreakdown:
    direction = 1.0 if a.net_flow_value >= 0 else -1.0
    breadth_net = a.funds_increasing - a.funds_reducing
    components = {
        "breadth": _sat(breadth_net, BREADTH_SATURATION) if breadth_net > 0 else 0.0,
        "net_flow": _sat(float(a.net_flow_value), float(FLOW_SATURATION_TRY)) if direction > 0 else 0.0,
        "persistence": _sat(a.persistence_periods, PERSISTENCE_SATURATION) if direction > 0 else 0.0,
        "new_positions": _sat(a.funds_new, NEW_POSITIONS_SATURATION),
        "conviction": max(0.0, min(1.0, a.avg_conviction)),
        "freshness": 0.5 ** (a.days_since_last_activity / FRESHNESS_HALF_LIFE_DAYS),
    }
    raw = 100 * sum(WEIGHTS[k] * v for k, v in components.items())
    mult = confidence_multiplier(a.flow_by_confidence)
    return ScoreBreakdown(raw=raw, adjusted=raw * mult, components=components, confidence_multiplier=mult)


def consensus_score(a: InstrumentActivity) -> ScoreBreakdown:
    """0 = every fund selling, 50 = split, 100 = every fund buying. New/exit shift the tilt."""
    active = a.funds_active
    if active == 0:
        return ScoreBreakdown(raw=50.0, adjusted=50.0, components={}, confidence_multiplier=1.0)
    agreement = (a.funds_increasing - a.funds_reducing) / active  # -1..1
    tilt = (a.funds_new - a.funds_exited) / max(active, 1) * 0.25
    raw = 50 + 50 * max(-1.0, min(1.0, agreement + tilt))
    mult = confidence_multiplier(a.flow_by_confidence)
    # Consensus is about *direction*; confidence only pulls it toward neutral, never negative.
    adjusted = 50 + (raw - 50) * mult
    return ScoreBreakdown(
        raw=raw,
        adjusted=adjusted,
        components={"agreement": agreement, "tilt": tilt, "funds_active": float(active)},
        confidence_multiplier=mult,
    )


def conviction_score(from_weight_pct: Decimal | None, to_weight_pct: Decimal | None) -> ScoreBreakdown:
    """How meaningful a move is *for that fund*: weight change and relative growth combined."""
    if to_weight_pct is None:
        return ScoreBreakdown(raw=0.0, adjusted=0.0, components={}, confidence_multiplier=1.0)
    w0 = float(from_weight_pct or 0)
    w1 = float(to_weight_pct)
    delta_pts = w1 - w0  # percentage points of the portfolio
    growth = (w1 / w0 - 1) if w0 > 0 else (2.0 if w1 > 0 else 0.0)  # NEW counts as +200%
    components = {
        "weight_delta_pts": delta_pts,
        "relative_growth": growth,
        "weight_delta_component": _sat(delta_pts, 5.0) if delta_pts > 0 else 0.0,  # +5pts = full
        "relative_growth_component": _sat(growth, 2.0) if growth > 0 else 0.0,  # +200% = full
        "size_component": _sat(w1, 8.0),  # 8% of the book = full
    }
    raw = 100 * (
        0.45 * components["weight_delta_component"]
        + 0.35 * components["relative_growth_component"]
        + 0.20 * components["size_component"]
    )
    return ScoreBreakdown(raw=raw, adjusted=raw, components=components, confidence_multiplier=1.0)
