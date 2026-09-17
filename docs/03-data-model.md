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
| `scores` | instrument (× fund) × type × as_of | `raw_score`, `adjusted_score`, `components` (incl. activity summary); `score_type` SMART_MONEY / CONSENSUS / CONVICTION (× fund) / CROWDING (from the funds' latest snapshots — `04`, `services/ownership.py`) |
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

## Insiders and issuer filings (Faz 4, US only)

SEC Form 4 is the report an insider (director, officer, ≥10 % owner) files within two business days of a transaction
in the issuer's stock. `services/insiders.refresh` (daily 08:30, `form4_daily`, gated by `sec_form4_enabled`; also
`instilens insiders`) reads each issuer's EDGAR submissions listing and stores three things:

| Table | Grain | Notes |
|---|---|---|
| `instruments.sec_cik`, `instruments.sec_form4_fetched_at` | instrument | the issuer's EDGAR CIK from the SEC's `company_tickers.json` (`instilens sec-ciks`, also at the start of the job for CIK-less issuers, committed before the batch is chosen; class-share tickers compared with `/` and `.` folded to `-`); when the issuer was last read, which orders the job stalest first. Share classes (GOOG / GOOGL, LEN / LEN-B) share the CIK: one listing, one set of Form 4s, every class stamped together |
| `disclosures` (kind `SEC_FORM4`) | one Form 4 / 4/A | `source_id` = accession, `raw_uri` = the filing index page, `payload` = the whole parsed document (issuer, reporting owners with relationship flags, both transaction tables, footnotes); PARSED on arrival. A 4/A supersedes, through `supersedes_id`, the live Form 4 of the same owner and issuer filed on its `dateOfOriginalSubmission`; failing that the earlier amendment naming the same original (a second 4/A replaces the first, never sits beside it); failing that the latest earlier document for the same period of report (within a week of the stated day, when one is stated). Within a day originals are stored before amendments, and an original that lands after its amendment (retried document) is flagged on arrival. The superseded document stays, flagged |
| `insider_transactions` | one transaction-table row | `disclosure_id` + `confidence` (lineage; EXACT — the insider's own report), `instrument_id` = the issuer the document names (an issuer's listing also carries the Form 4s it files as a 10 % owner of another company: those rows go to that company's instrument, or nowhere), `insider_cik` / `insider_name` of the attributed owner — a joint filing (a director and their trust, a 10 % owner group) names several owners for one set of rows: the natural person flagged director or officer, else the first owner — `roles` (comma-joined director, officer, ten_percent_owner, other: the union over the filing's owners) / `title` (the attributed owner's, else the first stated), `transaction_date`, `filed_at`, `code` as filed, `acquired` (A/D), `shares`, `price` (NULL when the filing states none — an RSU settlement's price cell holds only a footnote), `post_shares`, `ownership` (D/I), `derivative` (row of the derivative table), `is_superseded`, `row_hash` (accession, owner, ordinal, fields — unique, so a re-run writes nothing). Holding rows of the filing are positions, not transactions, and are not stored |
| `sec_filings` | issuer × accession | the listing index: `form` (4, 4/A, 8-K, 10-K, 10-Q), `filed_at`, `period` (report date), `items` (8-K item codes, e.g. `["2.02", "9.01"]`), `primary_document`, `url` (filing index). The documents themselves are not stored |

Transaction codes are the meaning of a row and are never flattened into "buy" / "sell": P open-market or private
purchase, S sale, A grant or award, M option exercise or RSU settlement, F shares withheld for tax, G gift, D
disposition to the issuer, C conversion, X exercise of an in-the-money derivative, J other (footnoted). The read models
(`/stocks/{symbol}/insiders`, the `insiders` block of stock_detail, the `get_insider_trades` tool) count only P rows as
buyers and S rows as sellers (distinct insiders; values = Σ shares × price of the rows that state a price) and list every
row with its code, accession and filing URL; `value` is shares × price at read time, nothing derived is stored. The
read models join the issuer's classes by CIK, so GOOG and GOOGL show the same rows and both get the cluster signal.
`/stocks/{symbol}/insiders` returns at most 200 rows and says `truncated: true` when the window holds more, with
`edgar_url` (the issuer's Form 4 list on EDGAR); `fetched_at` null on either endpoint means the issuer has not been read
yet — never "no activity" / "no filings". Refresh universe: US instruments seen in a 13F position change within 400
days or on a watchlist, stalest first, at most `sec_form4_max_issuers` (200, admin-editable) per run — chosen among the
instruments that carry a CIK, so a ticker the SEC map does not know (ADR variants, preferreds, delisted names) is
reported as skipped and never holds a slot; `sec_form4_days_back` (120) days of listing per issuer (the window is cut
before any cap; EDGAR's `recent` block holds at least a year, a longer window logs a warning). CUSIP placeholders have no
ticker to map and are left out. Each issuer's writes run in a savepoint and are committed as soon as it is done. TR
instruments answer `supported: false` — KAP insider filings come later. Test fixtures are real EDGAR documents
(`backend/fixtures/sec/form4/`, sources in `fixtures/sec/README.md`).

## Ownership and fund overlap (Faz 5)

No new table. `/stocks/{symbol}/ownership`, `/funds/overlap` and the CROWDING score rows (`scores.score_type =
CROWDING`) are read models over `portfolio_snapshots` × `snapshot_holdings` — each fund's **latest** snapshot on or
before the reference day (one row per fund), `position_changes` for the holder's last move (the change whose
`to_snapshot_id` is that snapshot) and `instruments.shares_outstanding` for the held share of the company. Holders
whose newest report is older than two reporting periods (TR 60 d, US 182 d) are left out and counted as
`stale_holders`. A position a report restates at quantity 0 is not a holding. Nothing derived is stored except the
CROWDING score row; `alert_rules.rule_type` gains PRICE_ABOVE / PRICE_BELOW with `params.price` and `params.since`
(the close date the rule starts from), evaluated against `market_prices` closes (`04`).

## Plans, portfolios, organisations and billing (Faz 6)

Migration `a3b4c5d6e7f8`. Every plan value the product shows comes from one place, `services/plans.FEATURES`
(FREE / PRO / PRO_PLUS × feature; a bool is on/off, an int is a cap) — `/auth/me` and `/billing/plans` hand it to
the UI as it stands there. Gating is the runtime setting `plans_enforced` (admin-editable, off by default): while it
is off nothing answers 402 and no cap applies, so existing accounts keep working; ADMIN is never gated. A gated
feature or an exhausted cap is `PlanLimit` → 402 `{"detail": "plan_limit", "feature", "plan", "limit", "upgrade"}`.
Rows already above a cap are never touched; a cap stops the next addition only.

| Table | Grain | Notes |
|---|---|---|
| `users.plan_source`, `users.plan_until` | user | where the account's own `plan` came from — `stripe` (a paid subscription) or `manual` (an admin grant); NULL on FREE — and a manual grant's expiry: past it the account *reads* as FREE (`plans.own_plan`), the row is not rewritten. The plan an account actually gets is `max(own plan, plans of the organisations it is an accepted member of)` (`plans.effective_plan`, source `own` / `manual` / `org`) |
| `portfolios` | user × portfolio | one market each, priced in that market's `currency`; caps per plan (`portfolios`) |
| `portfolio_positions` | portfolio × instrument | the user's own numbers: `quantity` Numeric(20,4) (fractional shares exist), `avg_cost` Numeric(20,6) per share or NULL, `opened_at`, `note`. Entered by hand, or — once the instrument has transactions in the portfolio — rewritten after every transaction change from them: the quantity still held and the FIFO average cost of the lots still held (a sale closes the oldest lots first; a buy's `fee` enters its lot, a sale's fee reduces proceeds only). A position sold to zero is removed, its transactions stay; a hand edit of a derived row is refused (409). Cap per plan (`portfolio_positions`) |
| `portfolio_transactions` | one buy / sell | `side` BUY/SELL, `quantity`, `price`, `traded_at`, `fee`, `note`. An oversell (more than held on its date, in trade order) is refused before anything is written |
| `organizations` | team | one per owner (`owner_user_id` unique). `plan` / `seats` mirror the owner's own plan as last read; what members inherit is resolved live from the owner (`plans.org_plan`), so an expiry or a cancellation reaches them without an event. Seats = the plan's `org_seats`, the owner's row included |
| `org_members` | seat | `invited_email` + no `user_id` while pending; the mailed ORG_INVITE token (`auth_tokens`, issued to the owner with `subject` = this row's id, 7 days, single use — `auth.mint_token`, which keeps the owner's other open invitations alive) turns it into a membership for the signed-in account with that address, verified where SMTP exists (`user_id`, `accepted_at`) — a link accepts its own seat only. Inviting says nothing about the address (no account probing); an account already in an organisation is turned away at accept. The owner is a row too (role OWNER) |
| `subscriptions` | grant | `provider` stripe (customer / subscription ids, `current_period_end`, `cancel_at_period_end` as Stripe reports them, `provider_event_at` = the newest event applied, so an older one delivered later is skipped; one row per provider subscription id) or manual (`note` = who and why — admin pages only, never `/billing/me`; `current_period_end` = the expiry); `status` TRIALING / ACTIVE / PAST_DUE / CANCELED / INCOMPLETE (not paid for: Stripe's incomplete / paused, an unpaid checkout — grants nothing); belongs to a user or an organisation. Never deleted: a replaced or ended grant is CANCELED and stays |
| `processed_webhooks` | provider × event id | every provider event applied once (`billing.apply_event`); a redelivery is acknowledged and changes nothing |

Read model `GET /portfolios/{id}` (`services/portfolio.detail`): every position priced at the latest stored close
(`market_prices` — a daily bar, never intraday; `close_date` says which), `market_value`, `cost_value`, `pnl_value`
/ `pnl_pct` against the average cost (null without one), `weight_pct` over the priced positions, the stored
SMART_MONEY / CONSENSUS / CROWDING scores (newest per symbol), the funds' 30-day counts on the symbol
(`funds_increasing_30d` / `funds_reducing_30d`, the same aggregation as `/moves` cut to the holdings) and, for US
symbols the Form 4 job has read, `insiders_net_90d`; `totals` add the priced positions (`unpriced` counts the rest);
`moves` lists the 30-day institutional moves on the held symbols with their parties. Nothing derived is stored.

Alerts: `PORTFOLIO_MOVE` is an implicit rule per (owner, held symbol) — never stored, never creatable — evaluated
with the others after every compute while the owner's plan includes portfolios; it fires once per (symbol,
period_end) when the latest period's snapshot diffs show ≥ 3 funds new / exited / increasing / reducing, in
descriptive text ("Portföyündeki THYAO: son dönemde 4 fon artırdı").

Billing (`services/billing`): Stripe through the official SDK only — a Checkout Session (`mode=subscription`, the
plan's price id, the user id and plan as metadata on the session and the subscription; `automatic_tax` and terms
consent when the deployment opted in), a Billing Portal session (plain, or `flow_data` subscription_update on the
live subscription for a plan change), and the webhook (raw body, `stripe.Webhook.construct_event` against the
signing secret; 400 on a bad signature). One live paid subscription per account: a second checkout is 409
`subscription_exists`. `checkout.session.completed` → the row ACTIVE and the account on the plan when
`payment_status` is paid (INCOMPLETE otherwise, the plan waits); `customer.subscription.updated` → status / plan (the
price id, metadata only when there are no items) / period / cancel-at-period-end — paused or incomplete grants
nothing; `invoice.payment_failed` → PAST_DUE with the plan kept (Stripe retries); `customer.subscription.deleted` →
CANCELED and the account back on FREE (or on another live subscription of the provider). Events carry `created`; one
older than the row's `provider_event_at` is skipped. A manual grant of a higher plan is not undone by the paid
subscription's events, and the admin page cannot grant or re-date an account whose paid subscription is live (400,
"managed by stripe"). Until the secret key, the webhook secret and both price ids are set, checkout / portal answer
503 "payments not configured" and `/billing/plans` reports `configured: false` with null prices — an amount is only
ever what Stripe's Price object states (cached an hour, with its `tax_behavior`), never typed in here. Admin grants
(`ManualProvider.grant`, the Users page) write a manual row with the note and the optional expiry; a plan set before
grants were recorded (no `plan_source`) is adopted as manual on its first re-date. The AI research quota per day
(`ai_research_per_day`) rides on the same counter store as the hourly AI budget (`rate_hits`, key `aiday:<user>`).
