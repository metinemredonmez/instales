# 08 · Warehouse (DuckDB)

The data layer as one self-contained analytical file: every fact and reference table the pipeline fills, the gold
views the product's read models are built from, and a `_meta` row that says where the file came from. Built by
`backend/src/instilens/services/warehouse.py`; opened with the DuckDB CLI, Python, pandas or anything that reads
DuckDB — no server, no credentials, no ORM.

What it is for: ad-hoc research over the whole history (SQL over every snapshot, every flow, every insider row),
notebooks and backtests, hand-offs to a partner analyst, and a reproducible "as of this build" copy the operational
database no longer is once the next pipeline run lands. What it is **not**: the product's source of truth (that is
the operational database, `docs/03-data-model.md`), a home for anything about users, and a place where `Decimal`
survives — see *Types*.

## Files

`settings.releases_dir/warehouse/` (default `backend/media/releases/warehouse/`, next to the desktop installers):

| File | What |
|---|---|
| `instilens-<YYYYMMDD>.duckdb` | one dated build; the date is the build's day in Europe/Istanbul (the scheduler's day). A second build on the same day replaces the first. |
| `latest.duckdb` | a byte-for-byte copy of the most recent build, swapped in atomically — a download in flight keeps reading the old inode. |

The newest **four** dated files are kept (`warehouse.KEEP_BUILDS`); older ones are deleted after every successful
build. `latest.duckdb` and anything else in the folder are never touched by the pruning. A build is written under a
hidden `.instilens-<date>.duckdb.<pid>.partial` name, verified (below) and only then renamed into place, so a
half-written file is never listed, downloaded or copied to `latest.duckdb`.

## Tables

Reference tables first, then the facts in the order the pipeline fills them (`warehouse.TABLES`):

| Table | Source model | Notes |
|---|---|---|
| `markets` | `MarketRow` | code, currency, timezone |
| `instruments` | `Instrument` | incl. `shares_outstanding` / `shares_as_of` from the fundamentals job, `sec_cik` |
| `institutions`, `funds` | `Institution`, `Fund` | |
| `disclosures` | `Disclosure` | **without `payload` and `parse_error`** — the lineage columns every fact row points at (`source`, `source_id`, `kind`, `published_at`, `raw_uri`, `raw_hash`, `supersedes_id`, `is_superseded`, `parse_status`) travel with the file; the verbatim filing stays in the operational database |
| `portfolio_snapshots`, `snapshot_holdings` | | the funds' reported books |
| `position_changes` | | snapshot diffs (always INFERRED) |
| `transaction_events`, `transaction_event_funds` | | disclosed buys/sells and the funds they relate to; `allocated_nominal` is NULL for a GROUPED event, as in the source (law 1: nothing is ever distributed) |
| `market_prices` | | daily bars, `source` = provider |
| `fundamental_snapshots` | | trailing metrics per instrument and fetch date (`metrics` is JSON) |
| `insider_transactions`, `sec_filings` | | Form 4 (US) and KAP insider (TR) rows, the EDGAR filings index |
| `signals`, `signal_outcomes`, `scores` | | the intelligence layer; `evidence` / `components` are JSON |
| `_meta` | — | one row: `built_at` (TIMESTAMP, UTC), `git_rev` (HEAD of the checkout, NULL in a container without git), `schema_version` (`warehouse.SCHEMA_VERSION`, bumped when a table, a mapping or a view changes), `app_version`, `alembic_revision` (the source database's head, NULL when the schema was created without Alembic), `source_dialect` (`sqlite` / `postgresql`), `row_counts` (JSON `{table: rows}`) |

Never included: `users`, `auth_tokens`, `waitlist` (e-mail addresses), `watchlists`, `watchlist_items`, `alert_rules`,
`notifications`, `live_events` (owner-private alert events), `brief_deliveries`, `push_subscriptions`, `portfolios` and
their positions/transactions, `organizations`, `org_members`, `subscriptions`, `processed_webhooks`, `app_settings`,
`audit_events`, `rate_hits`, `pipeline_runs`, `releases`, `release_files`, `news_*`, `ai_notes`, `searchable_texts`,
`fundamentals` (the statement lines — ask if a notebook needs them). The test suite pins this list exactly
(`test_type_mapping_and_excluded_columns`): a new model fails it until it is added to `warehouse.TABLES` or to the
excluded set — nothing reaches the file by default.

### Types

| SQLAlchemy | DuckDB | Note |
|---|---|---|
| `Integer`, `BigInteger` | `BIGINT` | quantities, nominal amounts, ids |
| `Numeric` (Money, Pct, scores) | `DOUBLE` | the operational store keeps `Decimal`; the warehouse is for analysis, not accounting — sums over millions of rows are fine, do not reconcile a ledger from it |
| `Boolean` | `BOOLEAN` | |
| `Date` | `DATE` | |
| `DateTime` | `TIMESTAMP` | naive, UTC (the source stores naive UTC) |
| `JSON` | `JSON` | the serialised document; query with `->>` / `json_extract` |
| `String`, `Text` | `VARCHAR` | |

Rows are copied in primary-key order, `warehouse.CHUNK_ROWS` (20 000) at a time: SQLAlchemy `yield_per` streams
the source, each slice becomes one pandas frame with explicit nullable dtypes and is `INSERT … SELECT CAST(…)`-ed
into the DuckDB table, so a `market_prices` table of millions of bars never sits in memory at once. NULL stays
NULL (a Form 4 row filed without a price is NULL, never 0).

## Gold views

Written as DuckDB SQL over the copied tables (`warehouse.VIEWS`), not through the Python services — and tested
against them: on the fixture chain every view reproduces the corresponding read model row for row.

**`v_ownership_latest`** — `services/ownership._holder_rows`. For every fund, its newest snapshot on or before the
reference date (`max(scores.as_of)`, the latest compute day; today before the first compute), one row per
fund × instrument with `quantity > 0`, joined to the position change that ends at that snapshot (`last_move`,
`last_move_period_end`; NULL when the snapshot is the fund's first). Columns: `market_code, instrument_id, symbol,
instrument_name, fund_id, fund_code, fund_name, institution_id, institution, quantity, market_value, weight_pct,
pct_of_shares` (against `instruments.shares_outstanding`, NULL while unknown)`, snapshot_id, disclosure_id, as_of,
confidence, last_move, last_move_period_end, is_stale, reference_date`. The app drops holders whose report is older
than two reporting periods (TR 60 days, US 182) and counts them as `stale_holders`; the view keeps them and flags
`is_stale` so the reader decides. The holders of a symbol:

```sql
SELECT fund_code, quantity, weight_pct, as_of, last_move
FROM v_ownership_latest WHERE symbol = 'THYAO' AND NOT is_stale ORDER BY quantity DESC;
```

**`v_flows_30d`, `v_flows_90d`** — `services/analytics.window_flows` for a 30- and a 90-day window ending on the
reference date (`as_of`; `window_start = as_of - N`). One row per instrument with any activity in the window:
`net_flow_value` (market currency), `net_qty`, `funds_increasing`, `funds_reducing`, `funds_new`, `funds_exited`,
`position_changes`, `transaction_events`, `window_start`, `as_of`. The laws of `docs/04-confidence-and-scoring.md`
are in the SQL:

- snapshot diffs whose `period_end` falls in `(window_start, as_of]` are canonical;
- a transaction event counts only while no later snapshot of any of its funds exists *as known on `as_of`*
  (the de-dup law, `pipeline._event_is_uncovered`), and only when not superseded;
- an event's value is its disclosed `net_value`, else `net_nominal × the latest close on or before the trade date`
  (`analytics.event_value`); an unpriced event moves `net_qty` but not `net_flow_value`;
- parties follow the breadth law: a fund seen in a diff or in an EXACT event is one party (`fund:<id>`), a GROUPED
  event is its institution (`inst:<id>`) — never a per-fund split;
- `funds_new` / `funds_exited` count the diffs' NEW / EXIT plus the entries (0 % or unknown → > 0 % on a buy) and
  full exits (→ 0 % on a sell) events disclose, a GROUPED one dropped when one of its funds is already counted.

A row whose window nets to exactly zero is in the view with `net_flow_value = 0`; the app's leaderboards show it in
neither list. The 30-day leaderboard as the radar ranks it:

```sql
SELECT symbol, net_flow_value, funds_increasing, funds_reducing FROM v_flows_30d
WHERE market_code = 'TR' AND net_flow_value > 0 ORDER BY net_flow_value DESC LIMIT 20;
```

**`v_fund_latest_books`** — `services/ownership.fund_books`: each fund's newest snapshot whatever its age (its
`as_of` says how old), positions with `quantity > 0`, with the snapshot's `total_value`, `source`, `confidence` and
`disclosure_id`. Two funds' overlap, the way `/funds/overlap` computes the weighted figure:

```sql
SELECT sum(least(a.weight_pct, b.weight_pct)) AS overlap_pct_weighted
FROM v_fund_latest_books a JOIN v_fund_latest_books b USING (symbol)
WHERE a.fund_code = 'TMV' AND b.fund_code = 'TI2';
```

(`/funds/overlap` answers NULL when either fund reports no weight for a common holding; `sum` skips NULLs — check
`count(*) FILTER (WHERE a.weight_pct IS NULL OR b.weight_pct IS NULL)` before trusting the number.)

## Building

| How | When | Lock |
|---|---|---|
| `instilens warehouse build` (`instilens warehouse list` shows what is on disk) | on demand | yes |
| Admin → Veri & pipeline → *Veri deposu (DuckDB)* → **Şimdi derle** (`POST /api/v1/admin/warehouse/build`) | on demand, background thread | yes |
| scheduler job `warehouse_weekly` | Sunday 03:30 Europe/Istanbul, after the 02:00 nightly compute wrote the week's scores | yes |

The weekly job runs only while the runtime setting **`warehouse_enabled`** is on — off by default, toggled from the
admin card or `PUT /api/v1/admin/settings {"warehouse_enabled": true}`; `INSTILENS_WAREHOUSE_ENABLED` is the `.env`
default. The CLI and the button ignore the switch.

The lock is a row under the reserved `_warehouse_lock` key of `app_settings` — the same unique-row pattern as the
pipeline lock (`api/hardening.acquire_pipeline_lock`), on the same store, so a second admin, a second uvicorn
worker, the CLI and the weekly job never write the same file twice at once: the button answers `started: false`,
the job logs *skipped* (it never queues), the CLI exits 1 naming who holds it. A build that died is taken over after
`STALE_AFTER` (2 hours). The last outcome (`started_by, started_at, finished_at, name, rows, seconds, error`) lives
under `_warehouse_status`; reserved keys are never listed or accepted by `/admin/settings`.

Verification, every build: the partial file is re-opened read-only, every table is counted against what was
written, every view is executed once (a definition that no longer binds to the copied schema fails the build, not
the first reader) and `_meta` must be exactly one row. Any mismatch raises `WarehouseError`, the partial file is
removed and nothing on disk changes. A build takes seconds on the beta database; the log lists rows and seconds per
table.

## Admin API (admin only — 401 without a token, 403 for a non-admin)

| Route | Answer |
|---|---|
| `GET /api/v1/admin/warehouse` | `{enabled, running, started_by, started_at, last, builds: [...], latest, keep}` — `builds` newest first, each `{name, built_at, size, rows, row_counts, git_rev, schema_version, url, error}`; `latest` is the same shape for `latest.duckdb` (null before the first build); a file whose `_meta` cannot be read is listed with `error` set and `rows` null; `enabled` re-applies the stored override first (the PUT may have landed on the other worker) |
| `GET /api/v1/admin/warehouse/download/{name}` | the file, streamed (`application/octet-stream`, `Content-Disposition: attachment`). Only `instilens-<YYYYMMDD>.duckdb` and `latest.duckdb` names resolve, and only inside the warehouse folder — anything else (`..`, an encoded slash, another extension, a dated name that does not exist) is 404. The route is bearer-authenticated, so the UI fetches it and saves the blob rather than linking `url` directly |
| `POST /api/v1/admin/warehouse/build` | `{started: true, running: true, started_by, …}` and the build runs in a background thread; `{started: false, running: true, …}` while another build holds the lock. A started build is an `admin.warehouse_build` audit event |

## Querying

DuckDB CLI (`brew install duckdb`):

```
$ duckdb --readonly media/releases/warehouse/latest.duckdb
D SELECT * FROM _meta;
D SELECT symbol, count(*) AS holders, sum(quantity) AS qty FROM v_ownership_latest
  WHERE market_code = 'TR' AND NOT is_stale GROUP BY symbol ORDER BY holders DESC LIMIT 10;
D SELECT insider_name, code, transaction_date, shares, price, post_pct_stake   -- post_pct_stake: KAP rows only (NULL on Form 4)
  FROM insider_transactions t JOIN instruments i ON i.id = t.instrument_id
  WHERE i.symbol = 'BURVA' AND NOT t.is_superseded ORDER BY transaction_date DESC;
D SELECT insider_name, code, transaction_date, shares, price                   -- a US issuer's rows sit under ONE of its share
  FROM insider_transactions t JOIN instruments i ON i.id = t.instrument_id     -- classes (GOOG / GOOGL share a CIK): join by CIK,
  WHERE i.sec_cik = (SELECT sec_cik FROM instruments WHERE symbol = 'GOOGL')   -- not by symbol, or a class reads as empty
    AND NOT t.is_superseded ORDER BY transaction_date DESC;
D SELECT evidence->>'$.consecutive_periods' AS periods, count(*) FROM signals WHERE signal_type = 'ACCUMULATION' GROUP BY 1;
```

Python / pandas:

```python
import duckdb

con = duckdb.connect("media/releases/warehouse/latest.duckdb", read_only=True)
meta = con.execute("SELECT built_at, git_rev, row_counts FROM _meta").fetchone()
flows = con.execute("SELECT * FROM v_flows_90d WHERE market_code = 'US'").df()  # a pandas DataFrame
books = con.execute("SELECT fund_code, symbol, weight_pct FROM v_fund_latest_books").fetchall()
con.close()
```

Open builds **read-only**: DuckDB lets any number of read-only connections share one file across processes, while a
single writer excludes everyone else — and a build is a snapshot, so edit a copy, never the file. Two dated builds
can be attached side by side to diff them (`ATTACH 'instilens-20260913.duckdb' AS old (READ_ONLY)`).

## Env checklist

`INSTILENS_RELEASES_DIR` (the parent folder, shared with the desktop installers) · `INSTILENS_WAREHOUSE_ENABLED=false`
(the weekly job's default; the admin toggle overrides it at runtime). The `duckdb` and `pandas` wheels are ordinary
dependencies (`uv sync`); nothing else to install. After a deploy that changes `warehouse.SCHEMA_VERSION`, run
`instilens warehouse build` once so `latest.duckdb` carries the new views.
