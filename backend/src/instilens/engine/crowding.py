"""Crowding score (instrument, 0..100). Pure: ownership figures in, a breakdown out — no session, no clock.

Crowding describes how many funds hold a stock and how the held quantity is spread among them — a count of positions
read from the funds' latest reports, never a valuation view ("crowded" is descriptive; it is not "overbought").

    raw = 100 × ( 0.35·holders + 0.25·held_pct + 0.20·concentration + 0.20·momentum )

| Component     | Input                                                  | Full marks at            |
|---------------|--------------------------------------------------------|--------------------------|
| holders       | funds holding the stock (latest snapshot each)         | 25 holders               |
| held_pct      | held quantity / shares outstanding × 100               | 30 %                     |
| concentration | 1 − HHI / 10000 (HHI over the holders' quantities)     | spread evenly (HHI → 0)  |
| momentum      | funds increasing − funds reducing in the score window  | +5 net parties           |

Saturation is the score engine's concave `sqrt(x / sat)`. When the share count is unknown, held_pct is skipped and
its weight is spread over the other three (their weights are rescaled to sum to 1) — the breakdown says so.
Levels: low < 35, medium 35..65, high > 65. Thresholds and weights are module constants — the published methodology
(docs/04-confidence-and-scoring.md), never per request.
"""

from __future__ import annotations

from dataclasses import dataclass

from instilens.engine.scoring import _sat

WEIGHTS = {
    "holders": 0.35,
    "held_pct": 0.25,
    "concentration": 0.20,
    "momentum": 0.20,
}

HOLDERS_SATURATION = 25  # funds holding the stock
HELD_PCT_SATURATION = 30.0  # % of shares outstanding held by the funds we see
MOMENTUM_SATURATION = 5  # funds increasing − funds reducing
HHI_MAX = 10000.0  # one holder owns everything

LEVEL_LOW_BELOW = 35.0
LEVEL_HIGH_ABOVE = 65.0
HELD_PCT_SKIPPED = "shares outstanding unknown — held_pct skipped, its weight spread over the other components"


@dataclass(frozen=True)
class CrowdingInputs:
    holders: int
    pct_of_shares: float | None  # held quantity / shares outstanding × 100; None when the share count is unknown
    top10_pct_of_held: float | None  # the ten largest holders' share of the held quantity (shown, not scored)
    hhi: float | None  # Herfindahl of holder quantities, 0..10000; None without holders
    funds_increasing: int
    funds_reducing: int
    window_days: int  # the score window the breadth counts come from (TR 30, US 100)
    stale_holders: int = 0  # funds left out because their newest report is too old (shown, not scored)


@dataclass(frozen=True)
class CrowdingBreakdown:
    """Same duck type as `scoring.ScoreBreakdown` for `pipeline._score_row`: `raw`, `adjusted`, `as_json()`. There is
    no confidence multiplier — every holder row is a fund's own report — so `adjusted` equals `raw`."""

    raw: float
    adjusted: float
    level: str
    weights: dict[str, float]  # the weights actually applied (held_pct 0 and the rest rescaled when skipped)
    components: dict[str, float]  # normalized 0..1 per component
    why: dict  # per component: raw input, normalized value, weight, contribution in points

    def as_json(self) -> dict:
        return {
            "raw": round(self.raw, 2),
            "adjusted": round(self.adjusted, 2),
            "level": self.level,
            "weights": {k: round(v, 4) for k, v in self.weights.items()},
            "components": {k: round(v, 4) for k, v in self.components.items()},
            "why": self.why,
        }


def level_of(score: float) -> str:
    """low < 35, medium 35..65 (inclusive), high > 65."""
    if score < LEVEL_LOW_BELOW:
        return "low"
    if score > LEVEL_HIGH_ABOVE:
        return "high"
    return "medium"


def effective_weights(shares_known: bool) -> dict[str, float]:
    """The published weights, or — without a share count — held_pct at 0 and the other three rescaled to sum to 1."""
    if shares_known:
        return dict(WEIGHTS)
    rest = sum(w for k, w in WEIGHTS.items() if k != "held_pct")
    return {k: (0.0 if k == "held_pct" else w / rest) for k, w in WEIGHTS.items()}


def crowding_score(i: CrowdingInputs) -> CrowdingBreakdown:
    shares_known = i.pct_of_shares is not None
    weights = effective_weights(shares_known)
    net_breadth = i.funds_increasing - i.funds_reducing
    components = {
        "holders": _sat(i.holders, HOLDERS_SATURATION),
        "held_pct": _sat(i.pct_of_shares, HELD_PCT_SATURATION) if shares_known else 0.0,
        "concentration": max(0.0, 1.0 - i.hhi / HHI_MAX) if i.hhi is not None else 0.0,
        "momentum": _sat(net_breadth, MOMENTUM_SATURATION) if net_breadth > 0 else 0.0,
    }
    contributions = {k: round(100 * weights[k] * v, 2) for k, v in components.items()}
    raw = 100 * sum(weights[k] * v for k, v in components.items())

    def part(key: str, raw_value, **extra) -> dict:
        return {"raw": raw_value, "normalized": round(components[key], 4) if not (key == "held_pct" and not shares_known) else None,
                "weight": round(weights[key], 4), "contribution": contributions[key], **extra}

    why = {
        "holders": part("holders", i.holders, saturation=HOLDERS_SATURATION),
        "held_pct": part("held_pct", i.pct_of_shares, saturation=HELD_PCT_SATURATION, **({} if shares_known else {"skipped": HELD_PCT_SKIPPED})),
        "concentration": part("concentration", i.hhi, top10_pct_of_held=i.top10_pct_of_held),
        "momentum": part("momentum", net_breadth, funds_increasing=i.funds_increasing, funds_reducing=i.funds_reducing,
                         saturation=MOMENTUM_SATURATION, window_days=i.window_days),
        "stale_holders": i.stale_holders,
    }
    return CrowdingBreakdown(raw=raw, adjusted=raw, level=level_of(round(raw, 2)), weights=weights, components=components, why=why)  # the level of the stored (rounded) score
