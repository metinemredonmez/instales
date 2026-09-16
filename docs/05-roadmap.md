# 05 · Roadmap

| Phase | Deliverable | Done when |
|---|---|---|
| 0 ✅ | Domain model, schema, confidence model | tests for parsers/engines pass |
| 1 ✅ | KAP ingestion (fixture), API adapter skeleton | `instilens ingest` stores raw disclosures idempotently |
| 2 ✅ | Parser + normalizer, amendments | EXACT/GROUPED assigned per law; superseded chain works |
| 3 ✅ | Position reconstruction | NEW/ADD/REDUCE/EXIT/HOLD with priced deltas |
| 4 ✅ | Signals + scores | radar shows accumulation/divergence/clusters from synthetic data |
| 5 ✅ | API + AI research | `/radar`, `/stocks`, `/funds`, `/events(+stream)`, `/screener`, `/research` |
| 6 ✅ | Frontend SPA (Login, Radar, Stock+chart, Fund, Live, Screener, Research, Watchlist, Alerts) + Tauri 2 shell | done on live API |
| 7 ✅ | Auth (argon2 + JWT), watchlist, alert rules (6 types), notifications, rate limiting, security headers | rules evaluated after each `compute`; dedup guarantees one notification per event |
| 8 ✅ | Alembic migrations | `instilens db init` = `alembic upgrade head` |
| 8b | Historical backfill + signal outcomes | 12 months replayed; outcome table computed nightly — blocked on real data |
| 9 | Official KAP API + licensed prices | `KapApiAdapter.map_detail` verified; scraping never used |
| G ✅ | Global | SEC EDGAR 13F client + parser → same tables; market-aware windows (TR 30D / US 100D); freshness badge; institution pages |
| 10 ✅ | Timeline · Fund-vs-Fund · Institutions · windows · signal outcomes · admin/entity review | all on live API |

**MVP acceptance chain** (must be true end to end, already exercised by `tests/test_pipeline.py`):
disclosure arrives → stored → entities resolved → normalized → confidence assigned → positions →
scores → (alert) → user can click through to the source.

Blocked on external inputs (not code):
0. **CUSIP coverage** — OpenFIGI mapped 187/207 top holdings; the long tail (4k+ rows) stays as unverified CUSIP instruments until a full CUSIP master is licensed or mapped incrementally from the admin review queue.
1. **Real KAP data** — prototype `KapPublicAdapter` already ingests real PYŞ transaction filings; the Veri Yayın Servisi contract replaces it for production (`KapApiAdapter.map_detail`). Fund portfolio PDFs, prose/PDF-only filings and avg-price extraction are done in the prototype.
2. **Licensed price feed** → replaces the Yahoo prototype (`instilens prices`) for production.
3. **`ANTHROPIC_API_KEY`** → live test of `/research`, then a small eval set of research questions.
4. **Hosting** — Postgres + API behind TLS; set `INSTILENS_ENVIRONMENT=production`, `JWT_SECRET`, `CORS_ORIGINS`; point desktop builds at it via `VITE_API_BASE`.
5. Entity review admin screen (unverified instruments/funds), Institution (PYŞ) page, push/e-mail delivery for notifications, Global (SEC 13F) adapter.
