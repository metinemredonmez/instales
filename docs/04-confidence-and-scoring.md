# 04 · Confidence & scoring methodology

## Confidence levels
| Level | Meaning | Multiplier |
|---|---|---|
| EXACT | one fund, one instrument, explicit amount | 1.00 |
| GROUPED | explicit amount attributed to a set of related funds; split unknown | 0.90 |
| INFERRED | derived from two portfolio snapshots; timing inside the period unknown | 0.80 |

The multiplier applied to a score is the |flow|-weighted average across the sources that fed it.
(First draft proposed 1.0/0.7/0.6; rejected — see `06-decisions.md`.)

## Smart Money Score (instrument, 0–100)
```
raw = 100 × ( 0.30·breadth + 0.25·net_flow + 0.15·persistence
            + 0.10·new_positions + 0.10·conviction + 0.10·freshness )
adjusted = raw × confidence_multiplier
```
| Component | Input | Saturation (full marks at) |
|---|---|---|
| breadth | funds increasing − funds reducing | 12 |
| net_flow | net institutional flow (₺), positive only | ₺500M |
| persistence | consecutive periods of positive net flow | 4 |
| new_positions | funds opening a first position | 4 |
| conviction | mean Conviction Score of increasing funds | 1.0 |
| freshness | days since last activity | half-life 14 days |

Saturation is concave (`sqrt(x / sat)`), so halfway to saturation earns ~0.7, not 0.5.

## Consensus Score (instrument, 0–100)
`50 + 50 × clamp(agreement + tilt)` where `agreement = (inc − red) / active`,
`tilt = 0.25 × (new − exited) / active`. Confidence pulls toward 50, never below.

## Conviction Score (fund × instrument, 0–100)
```
0.45 · sat(weight_delta_pts, 5)  +  0.35 · sat(relative_growth, 200%)  +  0.20 · sat(new_weight, 8%)
```
A NEW position counts as +200% relative growth. `1.8% → 7.4%` scores ~96.

## Signals
| Signal | Rule | Strength |
|---|---|---|
| ACCUMULATION / DISTRIBUTION | ≥3 consecutive periods of same-sign net flow | 50 + 10·extra periods + 10·growing periods |
| NEW_POSITION_CLUSTER / EXIT_CLUSTER | ≥3 distinct funds NEW/EXIT in the window | 40 + 12·(funds − 2) |
| POSITIVE / NEGATIVE_DIVERGENCE | |price Δ| ≥ 5% and |holdings Δ| ≥ 10% in opposite directions | 40 + |price Δ| + |holdings Δ| |

All thresholds are module constants in `engine/signals.py` and `engine/scoring.py`; changing one is a
methodology change and must be reflected here.

## Outcomes (credibility layer)
Each signal stores price at detection; `signal_outcomes` gets +7/30/90D returns, max return and
max drawdown. This is how we answer "do InstiLens signals work?" with data.
