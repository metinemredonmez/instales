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

## Insider purchase cluster (INSIDER_BUY_CLUSTER, US and TR)

Source: the `insider_transactions` rows (see `03-data-model.md`) — SEC Form 4 on US issuers, the KAP "Pay Alım Satım
Bildirimi" of directors, executives and shareholders on BIST. The signal fires for an issuer when at least
`CLUSTER_MIN_INSIDERS = 3` **distinct insiders** made **purchases at market** — Form 4 transaction code P in the
non-derivative table; on KAP an ALIŞ row, stored as P — inside the `CLUSTER_WINDOW_DAYS = 30` days ending on the compute
day. Grants and awards (A), option exercises and RSU settlements (M), shares withheld for tax (F), gifts (G), sales (S) and
every derivative-table row never count: only a P row is a decision to pay market price for the stock. Nor does the
company's purchase of its own shares (KAP rows with `roles = "issuer"` — a buyback is not an insider's decision; the
read model keeps those rows out of the detector's input). Rows of a superseded filing (replaced by a 4/A, or by a KAP
"Düzeltme") are ignored; the amendment's rows count instead. An insider is a person, not a filing: a joint Form 4 names
every reporting owner (a director and their trust, a fund and its general partner), and two filings whose owner sets
overlap count as one insider, whichever owner led each.

**KAP identity.** KAP publishes no identifier for a person or a company — only the name as filed — so on BIST a
"distinct insider" is a distinct `party_key`: "k" + 9 hex of the folded name (`parsing/kap_insider.party_key`; case,
spacing and diacritics removed, so the form's "MEHMET SÖNMEZ" and the page's "Mehmet Sönmez" are one party). The limits
follow from that and are part of the methodology: two different persons with the same name merge into one insider, and
one person whose name is spelled two ways ("Ahmet Yılmaz" / "A. Yılmaz") splits into two. The adapter reads the name
without its title or honorific so the same person's issuer-filed and MKK-relayed filings key alike, but it cannot repair a
filer's own spelling.

```
value    = Σ shares × price over the window's P rows that state a price   (unpriced rows count in breadth, not in value —
                                                                            most KAP filings state only a price range)
strength = round( 100 × min(1, insiders / 5) × ( 0.5 + 0.5 × min(1, value / 1,000,000) ) )
```
| insiders | value | strength |
|---|---|---|
| 3 | 0 known | 30 |
| 3 | ≥ 1M | 60 |
| 4 | 500k | 60 |
| 5+ | ≥ 1M | 100 |

`CLUSTER_VALUE_SATURATION = 1,000,000` is a plain figure **in the market's currency** — $1M on US, ₺1M on TR — and is
not converted: three buyers whose stated purchases total ₺1M score 60 on BIST exactly as three totalling $1M do on
NASDAQ, although ₺1M is a far smaller sum. This is deliberate for now: breadth is the main factor (five buyers saturate
it) and the value term only lifts a cluster the more the insiders paid; it never zeroes one whose filings state no
prices. A per-market saturation would be a scoring change and would be recorded here. Confidence is the weakest of the
purchases' rows — EXACT for every Form 4 and KAP row today: each purchase is the insider's own report, with its accession
or disclosure index. `window_start` is the first purchase in the window; the row is episodic like every other signal (an
open episode is extended day by day, a second compute of the same day updates the row in place — its id never changes)
and `evidence` lists `insiders`, `names`, `value`, `unpriced`, `purchases`, `since`, `accessions` (KAP: the disclosure
indexes) and `window_days`. Constants live in `engine/insiders.py`. The signal feeds the generic SIGNAL alert rule and
the dedicated INSIDER_BUY_CLUSTER rule (implicit for every watched stock on both markets, refused on a fund; text:
"3 insiders bought on the open market in the last 30 days" on US, "3 insiders bought shares in the last 30 days (KAP)"
on BIST, where the filing states no venue — descriptive, never advice), keyed by the episode row so an ongoing cluster
notifies once.

## Crowding Score (CROWDING, instrument, 0–100)

How many funds hold a stock and how the held quantity is spread among them — a count of reported positions, never a
valuation view: "crowded" is descriptive ("14 funds hold it, the ten largest hold 80 % of what funds hold"), it is not
"overbought" and never a verdict. Inputs come from `services/ownership.py`: each fund's **latest** portfolio snapshot on
or before the compute day (one row per fund, never two dates of one fund), leaving out — and counting as
`stale_holders` — funds whose newest report is older than two reporting periods (`PERIOD_DAYS × 2`: TR 60 days,
US 182); the held share of the company uses `instruments.shares_outstanding` (the fundamentals job); breadth momentum
is the score window's `funds_increasing − funds_reducing` (the same parties the Smart Money Score counts).

```
raw = 100 × ( 0.35·holders + 0.25·held_pct + 0.20·concentration + 0.20·momentum )
adjusted = raw                                     (no confidence multiplier: every holder row is a fund's own report)
```
| Component | Input | Full marks at |
|---|---|---|
| holders | funds holding the stock (latest non-stale snapshot each) | 25 |
| held_pct | Σ holder quantity / shares outstanding × 100 | 30 % |
| concentration | 1 − HHI / 10000, HHI = Σ (holder's share of the held quantity in %)² | spread evenly (HHI → 0) |
| momentum | funds increasing − funds reducing in the score window, positive only | +5 |

Saturation is the score engine's concave `sqrt(x / sat)`. **Shares unknown** (no fundamentals yet): `held_pct` is
skipped and its 25 % is spread over the other three (weights rescaled to 0.4667 / 0.2667 / 0.2667, still summing to
1); the breakdown says so (`why.held_pct.skipped`, `weight: 0`). Levels: **low < 35**, **medium 35..65**, **high > 65**
(of the stored two-decimal score). Constants live in `engine/crowding.py`.

Stored as `scores` rows with `score_type = CROWDING`, `fund_id NULL`, one per instrument at least one fund holds in a
non-stale report as known on the compute day (an instrument without window activity still gets its row — momentum
is simply 0); written by `compute_intelligence` from one holder query per market, so the stock page (the header chip
from `stock_detail.scores.CROWDING` and the ownership section from `/stocks/{symbol}/ownership`) and the ownership AI
tools (`get_stock_ownership`, `get_crowding_score`) read one number. Radar and the Screener list the Smart Money and
Consensus scores only. `components` holds
`raw`, `adjusted`, `level`, the effective `weights`, the normalized `components` and `why` — per component its raw
input, normalized value, weight and contribution in points (`Σ contribution = raw`), plus `stale_holders`.

The ownership page also reports `top10_pct_of_held` (the ten largest holders' share of the held quantity) and `hhi`;
`/funds/overlap` (2..6 funds of one market, latest snapshot each) gives `overlap_pct_symbols` = common / union × 100
and `overlap_pct_weighted` = Σ min(w_a, w_b) over the common symbols in percentage points of a book — null when either
fund reports no weight for one of them (a partial sum would read as a smaller overlap than the truth).
`/funds/{code}/compare/{other}` carries the same two figures.

## Price alerts (PRICE_ABOVE / PRICE_BELOW)

Explicit rules only (never implicit for a watched stock), `params: {"price": 0 < number ≤ 1e9, "since": date}`,
stocks only. Evaluated against `market_prices` **daily closes** — the header quote feed carries the market strip, not
stocks, so an intraday crossing is seen at the next close. A rule fires on the close date that crossed the threshold
(that close is beyond it and the previous close was not, or there is no previous close) and once per crossing: the
dedup key is `{rule.id}:{close_date}`. Every evaluate walks the last `PRICE_LOOKBACK` (10) closes pairwise, not only
the newest pair, so a crossing still fires when two closes land between two runs (a lagging feed, a skipped compute);
the close before the window is only ever a "previous", never a crossing of its own. `since` is the latest close known
when the rule was created (today when the stock has no close yet): closes before it never count, so a rule created
while the close is already beyond its threshold waits for the next crossing, unless that latest close is itself the
crossing. Text is descriptive — "ASELS: kapanış 123,4 ₺ ile eşik 120 ₺ üzerinde" / "ASELS: close 123.4 ₺ is above the 120 ₺
threshold" — never a call to act.
