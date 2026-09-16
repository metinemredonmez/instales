# 02 · Architecture

## Stack decision

| Layer | Choice | Why |
|---|---|---|
| Backend | **Python 3.12+, FastAPI, SQLAlchemy 2, Pydantic 2** | Parsing, analytics and AI tooling are all Python-native; one language for engines + AI |
| Database | **PostgreSQL** (SQLite for dev/tests) | Relational facts with lineage; Postgres FTS is enough for search; no Elasticsearch on day 1 |
| Jobs | `instilens` CLI stages on cron/APScheduler | One polling loop does not justify Celery + Redis. Add `arq` when fan-out is real |
| Realtime | **SSE** (`/events/stream`) | One-way feed; no WebSocket needed |
| AI | **Claude API (claude-opus-5) via Anthropic SDK tool runner**; provider interface for a local model later | Model is the *interface*; tools read our tables; audit trail per answer |
| Frontend | **Vite + React + TypeScript + Tailwind + shadcn/ui**, TanStack Query, TradingView lightweight-charts | A pure SPA runs unchanged in the browser, inside Tauri and (Tauri 2) on iOS/Android — no SSR to fight |
| Desktop | **Tauri 2** wrapping the same SPA | Small binary, native shell; backend stays remote (hosted API) for MVP; a local model can later run beside the desktop app |
| Mobile | Later; Tauri 2 mobile or a thin React Native — same API | |
| Storage | S3-compatible bucket for raw PDFs/XML | |

Why not Next.js: SSR/server components add nothing for an authenticated dashboard and complicate
Tauri packaging. Why not Electron: 10× bundle, no mobile path.

## Data flow

```
                ┌─────────┐          ┌──────────┐
                │   KAP   │          │ SEC (v2) │
                └────┬────┘          └────┬─────┘
                     │ SourceAdapter.fetch()
                     ▼
              disclosures (raw, hashed, superseded-chain)
                     │ parse_pending()
                     ▼
   ┌──────────────── EntityResolver ────────────────┐
   ▼                                                ▼
transaction_events (+ event_funds)        portfolio_snapshots (+ holdings)
   EXACT / GROUPED                                  │ rebuild_positions()
   │                                                ▼
   │                                       position_changes (INFERRED)
   └──────────────┬─────────────────────────────────┘
                  │ compute_intelligence(as_of)   ← market_prices
                  ▼
           signals · scores (with components)
                  │
        ┌─────────┴──────────┐
     FastAPI              AI research (tools)
        │                      │
   Web / Tauri / alerts    natural-language answers + audit trail
```

## Laws enforced in code
- **Allocation law** — `parsing/kap_share_transaction.py`: one fund ⇒ EXACT & allocated; else GROUPED & `NULL`.
- **De-dup law** — `pipeline._event_is_uncovered`: an event counts only if its trade date is after
  the latest snapshot of *all* related funds. Conservative: undercount beats double count.
- **Baseline law** — the first snapshot of a fund produces no position changes.
- **Supersede law** — `_store_raw`: `amends_source_id` marks the old disclosure and its events superseded.
- **Breadth law** — a fund is one party whether seen via snapshot or EXACT event; a GROUPED event is
  one party (the institution).

## KAP integration
- Prototype: `KapFixtureAdapter` reads canonical JSON (`domain/schemas.py`). Also the replay/backfill tool.
- **Prototype, real data:** `KapPublicAdapter` (`INSTILENS_KAP_ADAPTER=public`) reads the public site's own JSON list
  (`POST /tr/api/disclosure/members/byCriteria`) and the detail page's embedded RSC payload for PYŞ
  "Pay Alım Satım Bildirimi" filings. Polite by design: one list call, ≤25 details/run, 1.5 s apart, never
  re-fetches known ids, commits each disclosure as it arrives, retries transient errors with backoff.
  Transaction numbers come from the table, else from prose (`rows_from_prose`), else from the PDF attachment
  (`numbers_from` records which). Weekly fund **Portföy Dağılım Raporu** PDFs are fetched for equity-focused
  funds (`disclosure/funds/byCriteria` → detail → `/tr/api/file/download/{objId}`) and parsed by
  `ingestion/kap/pdr_pdf.py` (equity block only; quantity validated by qty × price ≈ value). Dev/validation
  only — MKK expects heavy consumers on the licensed service.
- Production: `KapApiAdapter` against the official Veri Yayın Servisi (`disclosures`, `disclosureDetail`,
  `lastDisclosureIndex`, …). Requires a Borsa İstanbul data-distribution agreement, API key and IP
  whitelisting. `map_detail()` is intentionally `NotImplemented` until field names are verified
  against the contract docs. **No scraping in production.**
- Scope of funds: start with equity and hedge (serbest) funds holding BIST equities.

## Global (SEC 13F)
`ingestion/sec/edgar_client.py` reads the public EDGAR submissions API and the 13F information-table XML
(User-Agent must identify the app). A 13F is a portfolio snapshot: filer = institution = "fund" (`CIK…`),
so it flows through the same snapshot → diff → INFERRED path as KAP portfolio reports. Options rows are
dropped; duplicate CUSIP rows are merged. `MARKET_WINDOW_DAYS` gives US a 100-day activity window
(quarter + 45-day filing lag). CUSIP→ticker comes from `fixtures/cusips_US.csv` (OpenFIGI); unmapped
CUSIPs become unverified instruments visible in the admin review queue.

## Entity review
Unknown symbols/funds/members are auto-created with `is_verified=False` so ingestion never blocks.
An admin list of unverified rows is part of phase 7 ops tooling.

## Market data
`market_prices` from Yahoo Finance in the prototype (`instilens prices --market TR|US`, `ingestion/prices/yahoo.py`,
delayed/unofficial) or a CSV; a licensed feed in production behind the same loader. Needed for flow
valuation, divergence and signal outcomes.

## Auth & hardening
- argon2id password hashes, HS256 JWT (7-day TTL) issued by `/auth/login|register`; `GET /auth/me` validates.
- Every `/api/v1/*` data route requires `Authorization: Bearer`; the SSE stream accepts `?token=` (EventSource cannot set headers).
- Per-IP rate limit on login/register (in-process; move to Redis behind replicas), security headers, CORS from settings.
- `INSTILENS_ENVIRONMENT=production` refuses to boot with the default `JWT_SECRET` and adds HSTS.
- Alerts: `AlertRule` → `services/alerts.evaluate()` after each compute → `Notification` with a `dedup_key`, so one event = one notification.

## Repo layout
```
backend/            Python package `instilens` (uv)
  src/instilens/
    domain/         enums, ORM models, Pydantic contracts
    ingestion/      SourceAdapter + KAP adapters
    parsing/        disclosure → normalized objects
    engine/         positions, signals, scoring (pure functions)
    services/       entities, pipeline (stages), analytics (read models)
    ai/             research engine: tools, Claude engine, local stub
    api/            FastAPI
  fixtures/         synthetic disclosures + prices
  tests/
frontend/           Vite + React SPA (src/pages, src/components, src/lib)
  src-tauri/        Tauri 2 desktop shell
docs/
```
