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
