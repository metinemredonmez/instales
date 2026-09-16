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

Consensus counts **parties**, not money: every party is one vote whatever its position size or flow value —
it is *not* size-weighted. A party is a fund seen in a snapshot diff or an EXACT event (one key, so a fund is
never counted twice) or, for a GROUPED event, the disclosing institution (breadth law). `active` is the number
of distinct parties that increased, reduced or held. Money only enters through the confidence multiplier.
`new` / `exited` are the same parties the NEW/EXIT clusters count — a snapshot diff's NEW/EXIT plus the
first-time entries and full exits disclosed by uncovered events — so the score components, the cluster signals
and the leaderboard's `funds_new` / `funds_exited` (`analytics.window_flows`) always agree.

## Conviction Score (fund × instrument, 0–100)
```
0.45 · sat(weight_delta_pts, 5)  +  0.35 · sat(relative_growth, 200%)  +  0.20 · sat(new_weight, 8%)
```
A NEW position counts as +200% relative growth. `1.8% → 7.4%` scores ~96.

## Signals
| Signal | Rule | Strength |
|---|---|---|
| ACCUMULATION / DISTRIBUTION | ≥3 consecutive periods of same-sign net flow | 50 + 10·extra periods + 10·growing periods |
| NEW_POSITION_CLUSTER / EXIT_CLUSTER | ≥3 distinct parties entering / fully exiting in the window (see below) | 40 + 12·(parties − 2) |
| POSITIVE / NEGATIVE_DIVERGENCE | |price Δ| ≥ 5% and |holdings Δ| ≥ 10% in opposite directions | 40 + |price Δ| + |holdings Δ| |

Cluster parties come from two sources. Snapshot diffs: a fund whose activity is NEW / EXIT. Transaction events
(only those not yet covered by a snapshot, per the de-dup law): a buy that takes ownership from 0 % (or unknown)
to above 0 % is an entry, a sell that takes it to 0 % is a full exit. An EXACT event's fund uses the same key as
a snapshot diff, so it is one party either way; a GROUPED event is its institution and is dropped when one of its
funds is already counted for that move. `evidence` lists `funds` (all parties), `from_snapshots` and `from_events`.

All thresholds are module constants in `engine/signals.py` and `engine/scoring.py`; changing one is a
methodology change and must be reflected here.

## Outcomes (credibility layer)
Each signal stores price at detection; `signal_outcomes` gets +7/30/90D returns, max return and
max drawdown. This is how we answer "do InstiLens signals work?" with data.
