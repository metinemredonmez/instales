# InstiLens

**Smart-money & institutional intelligence platform.** Turkey (KAP) + Global (SEC 13F) on one engine.

Answers one question: *"Profesyonel para nereye gidiyor?"* — not "what does this fund hold" but
*who is accumulating what, since when, with how much conviction, and how sure are we*.

```
KAP disclosure → raw store → parser → entity resolution → normalized facts (with confidence)
→ position reconstruction → signals & scores → API / AI research → UI, alerts
```

## Status

| Phase | Scope | State |
|---|---|---|
| 0 | Domain model, schema, confidence model | ✅ `backend/src/instilens/domain` |
| 1 | KAP ingestion (fixture adapter; official API adapter stub) | ✅ `ingestion/kap` |
| 2 | Parsers + normalizer (share transactions, portfolio reports, amendments) | ✅ `parsing/` |
| 3 | Position reconstruction (NEW/ADD/REDUCE/EXIT/HOLD) | ✅ `engine/positions.py` |
| 4 | Signals (accumulation, distribution, clusters, divergence) + scores (Smart Money, Consensus, Conviction) | ✅ `engine/` |
| 5 | FastAPI (`/radar`, `/stocks`, `/stocks/{s}/series`, `/funds`, `/events`, `/events/stream`, `/screener`, `/research`) + JWT auth, rate limiting, security headers | ✅ `api/` |
| 5b | AI research engine (Claude tool-runner, tool-grounded answers, audit trail) | ✅ `ai/` — live call untested here (no API key on this machine) |
| 6 | Frontend — Vite + React + TS + Tailwind + shadcn; Login, Radar, Stock (+ price/holdings chart), Fund, Live (SSE), Screener, Research, Watchlist, Alerts; light/dark | ✅ `frontend/` |
| 6b | Tauri 2 desktop shell (`frontend/src-tauri`, `cargo check` passes) | ✅ scaffold — `npm run desktop:dev` |
| 7 | Watchlist + alert rule engine (6 rule types, dedup, runs after every compute) | ✅ `services/alerts.py` |
| 8 | Alembic migrations | ✅ `migrations/` |
| 9 | Signal outcomes (+7/30/90D, max return, drawdown) + signal performance table | ✅ `services/outcomes.py` |
| 10 | Timeline, Fund-vs-Fund overlap, Institution pages, Today/7D/3M windows, data-freshness badge | ✅ |
| 11 | Admin: roles, user management, entity review (unverified instruments/funds), failed parses | ✅ `/admin` |
| G | **Global: SEC EDGAR 13F** — live client (free, no contract) + 9 real filings as fixtures (Berkshire, Bridgewater, Renaissance), CUSIP→ticker via OpenFIGI | ✅ `ingestion/sec` |
| — | Historical backfill (12 months TR), licensed price feeds, KAP official API mapping | ⏳ external inputs |

## Quickstart

```bash
cd backend
uv sync
uv run instilens run                         # migrate → KAP + SEC ingest → parse → Yahoo prices → positions → signals → scores → alerts → outcomes
uv run instilens api                         # http://127.0.0.1:8000/docs
uv run pytest -q                             # 76 tests, SQLite in-memory, no network
cd ../frontend && npm install && npm run dev  # http://localhost:5173 (proxies /api to :8000) → register on the login screen — Node ≥ 22
npm run desktop:dev                          # Tauri window (needs Rust toolchain)
```

Environment: `cp backend/.env.example backend/.env` — every `INSTILENS_*` setting with a one-line comment (data sources, AI budget,
trusted proxies, ElevenLabs voices, live TV channels, desktop releases, backup passphrase/remote). As copied it boots in development;
production refuses to start until `INSTILENS_JWT_SECRET` is replaced (≥ 32 random chars). `ANTHROPIC_API_KEY` keeps its plain name.

Toolchain: Python 3.12 + `uv`; **Node 22** (CI, the desktop workflow and `server-setup.sh` all use 22 — `frontend/package.json`
should carry `"engines": { "node": ">=22" }` so an older local Node fails loudly instead of at build time).

Real KAP (prototype, polite): `INSTILENS_KAP_ADAPTER=public uv run instilens ingest && uv run instilens parse && uv run instilens compute` — transactions + weekly fund portfolio PDFs.
Prices (prototype, Yahoo): `uv run instilens prices --market TR` / `--market US`.
Live SEC pull: `INSTILENS_SEC_ADAPTER=edgar uv run instilens ingest --market US` then `uv run instilens compute`. Refresh fixtures + CUSIP map: `uv run python scripts/build_sec_fixtures.py`.
```bash
ANTHROPIC_API_KEY=... uv run instilens ask "Son 30 günde fiyatı düşerken fonların topladığı hisseler?"
```

**No mock data in the app.** `instilens run` pulls real sources only: KAP (public-site prototype adapter until the
licensed Veri Yayın Servisi is signed), SEC EDGAR (free), Yahoo Finance prices (delayed). Synthetic fixtures under
`backend/fixtures/kap` are used by the test-suite only.

## Deploy

- `backend/Dockerfile` (API + worker, same image) · `frontend/Dockerfile` (nginx SPA, proxies `/api`)
- `infra/compose.yml` for Docker/Podman hosts; `infra/container-up.sh` for Apple `container` / Berthly (no compose)
- `instilens scheduler` = background worker (KAP every 5 min in the evening rush, SEC daily, prices at close, nightly compute)
- Copy `infra/.env.example` → `infra/.env`; set `POSTGRES_PASSWORD`, `INSTILENS_JWT_SECRET`, `ANTHROPIC_API_KEY`
- Plain Ubuntu box (pm2 + nginx): `infra/pm2/README.md` — `deploy.sh` (pull → build → migrate → reload → `/health` check, automatic
  rollback to the previous commit on failure), nightly `backup.sh` (DB + desktop installers + encrypted `.env`, optional off-site copy)
- Desktop installers: `.github/workflows/desktop.yml` builds all platforms and uploads them to our release store when the
  `INSTILENS_RELEASE_UPLOAD_KEY` secret is set (GitHub Release otherwise); `scripts/desktop-release.sh` does the same from a Mac

## Docs

- `docs/01-product-brief.md` — what we are building and what we are not
- `docs/02-architecture.md` — stack (backend / frontend / desktop / AI), data flow, KAP integration
- `docs/03-data-model.md` — tables and the laws they enforce
- `docs/04-confidence-and-scoring.md` — EXACT / GROUPED / INFERRED and the score formulas
- `docs/05-roadmap.md` — phases and acceptance criteria
- `docs/06-decisions.md` — where this design deliberately differs from the first draft
