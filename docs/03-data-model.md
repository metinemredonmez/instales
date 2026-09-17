# 03 · Data model

Source of truth: `backend/src/instilens/domain/models.py`.

| Table | Grain | Notes |
|---|---|---|
| `markets` | market | TR, US |
| `instruments` | market × symbol | `is_verified=False` when auto-created |
| `institutions` | disclosing entity | PYŞ / 13F filer; `source_ref` = KAP member OID / CIK |
| `funds` | fund | TEFAS code; belongs to an institution (re-parented once known) |
| `disclosures` | raw filing | verbatim `payload`, `raw_hash`, `supersedes_id`, `parse_status` |
| `transaction_events` | normalized buy/sell | `confidence` EXACT/GROUPED, `is_superseded` |
| `transaction_event_funds` | event × fund | `allocated_nominal` NULL unless EXACT |
| `portfolio_snapshots` | fund × as_of | corrected report replaces same-date snapshot |
| `snapshot_holdings` | snapshot × instrument | qty, value, weight |
| `position_changes` | fund × instrument × period | NEW/ADD/REDUCE/EXIT/HOLD, always INFERRED, `delta_value` priced at period end |
| `market_prices` | instrument × date | closes |
| `fundamentals` | instrument × kind × period_kind × period_end | reported statement lines (`items` JSON, canonical keys), `currency`, `source`, `fetched_at` |
| `fundamental_snapshots` | instrument × as_of | trailing metrics (`metrics` JSON), `currency` (reporting), `quote_currency` (listing), `source`, `fetched_at` |
| `signals` | instrument × type × window | `evidence` JSON shown verbatim as "why" |
| `scores` | instrument (× fund) × type × as_of | `raw_score`, `adjusted_score`, `components` (incl. activity summary) |
| `signal_outcomes` | signal | +7/30/90D returns, max return, drawdown |
| `watchlists`, `watchlist_items`, `alert_rules`, `notifications` | user layer | phase 7 |

Time semantics are bi-temporal: `published_at` (when KAP published) vs `effective_date` (trade date)
vs `as_of` (snapshot date). Never mix them in a window filter without saying which one you mean.

Normalized transaction event (parser output):
```json
{"market":"TR","source":"KAP","source_id":"1608450","instrument_symbol":"ANELE",
 "institution_ref":"MOID-TERA","fund_codes":["TLY","TMV"],"side":"BUY",
 "buy_nominal":46526835,"sell_nominal":0,"net_nominal":46526835,"avg_price":"12.5",
 "net_value":"581585437.5","effective_date":"2026-09-10","published_at":"2026-09-11T20:10:00",
 "ownership_before_pct":"0.0","ownership_after_pct":"17.56","confidence":"GROUPED"}
```

## Fundamentals (Faz 3)

Reported financial statements and trailing metrics per instrument, pulled weekly (Sunday 06:00, `fundamentals_weekly`)
from the fundamentals provider (`ingestion/fundamentals/`; only Yahoo exists, `fundamentals_provider=yahoo`) and written
by `services/fundamentals.refresh`. `fundamentals` holds one row per instrument × kind (`income` / `balance` / `cashflow`)
× period kind (`annual` / `quarterly`) × `period_end`; `items` carries only the canonical keys of the kind — income:
`revenue, cost_of_revenue, gross_profit, operating_income, ebitda, pretax_income, net_income, eps_diluted,
interest_expense`; balance: `total_assets, total_liabilities, equity, total_debt, cash, current_assets,
current_liabilities`; cashflow: `operating_cf, capex, free_cf, dividends_paid, share_repurchase` — as absolute amounts in
`currency` (the filer's reporting currency; the market default is assumed and logged when the provider does not state
one). A line the filing did not carry is `null`, never computed; the single exception is `free_cf = operating_cf + capex`
when the provider prints both parts but not the total. `fundamental_snapshots` holds the provider's trailing metrics
(market cap, EV, P/E, forward P/E, P/B, P/S, EV/EBITDA, margins, ROA/ROE, TTM revenue/EBITDA/net income/EPS, dividend
yield, payout, beta, 52-week range, shares outstanding, float, short interest) as stated on `as_of`, percentages already
×100, in two currencies: market cap, EV, the 52-week range and EPS are priced off the listing and sit in `quote_currency`
(TRY for THYAO), the TTM revenue / EBITDA / net income come from the filings and sit in `currency` (USD for THYAO, the
same reporting currency as its statements). Forward P/E is the one figure based on consensus estimates rather than
reported numbers and is labelled as such. The snapshot also updates `instruments.shares_outstanding` / `shares_as_of`.
Every row names its `source` and `fetched_at`. Analyst recommendation / rating / target-price fields are never ingested
(SPK rule). The ratios on `/stocks/{symbol}/fundamentals` (`derived`: margins, debt-to-equity, YoY growth) are computed
at read time from the newest statement and the one a year earlier — nothing derived is stored. Refresh universe:
instruments of the market seen in a position change or transaction within 400 days or on a watchlist, stalest first
(never fetched, then oldest `fetched_at`), at most `fundamentals_max_instruments` (300, admin-editable) per market and
run so a weekly job walks the whole universe over a few weeks; CUSIP placeholders are never sent to a provider.
