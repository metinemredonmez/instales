# 06 · Decisions — where this design differs from the first (GPT) draft

| # | Topic | First draft | Decision | Why |
|---|---|---|---|---|
| 1 | Event vs snapshot overlap | not addressed | De-dup law: snapshot is canonical for covered periods; events count only after the last covering snapshot | Same purchase would otherwise be counted twice (same-day event + monthly diff) |
| 2 | Corrections | not addressed | `supersedes_id` chain, superseded facts kept | KAP "Düzeltme" is common; audit trail matters |
| 3 | Confidence multipliers | 1.0 / 0.7 / 0.6 | 1.0 / 0.9 / 0.8 | Most data is INFERRED; 0.6 would cap the score near 60 structurally. INFERRED is coarse in timing, not wrong in amount |
| 4 | `source_lineage` table | separate table | `disclosure_id` + `confidence` on every fact row | Lineage is a property of the row, not a join |
| 5 | Breadth counting | fund counts | party counting: fund (snapshot or EXACT) / institution (GROUPED) | Avoids counting TMV twice and never inflates GROUPED into N funds |
| 6 | Jobs | Celery + Redis | CLI stages on cron/APScheduler | One polling loop; add `arq` when fan-out exists |
| 7 | Repo | apps/services/workers/packages | `backend/`, `frontend/`, `desktop/`, `docs/` | Small team; four-layer monorepo slows iteration |
| 8 | Frontend | Next.js | Vite React SPA | Same bundle for web, Tauri desktop, Tauri mobile; no SSR needed for an authenticated dashboard |
| 9 | AI | phase 2 | ships with MVP as the *interface* (tools over deterministic data), provider-swappable | User priority; safe because the model never computes — it reads |
| 10 | First snapshot | ambiguous | baseline, no changes emitted | Otherwise every fund's whole book looks like a buying spree on day one |
| 11 | Naming | InstiLens | keep as company/global working name; Turkish consumer brand TBD | "Insti" does not read well in Turkish; clearance pending |
| 12 | Global data source | "v2, later" | SEC EDGAR free API shipped now, before the KAP contract | No contract needed; proves the multi-market schema with real data on day one |
| 13 | Activity window | one 30D window | per-market (`MARKET_WINDOW_DAYS`) | 13F is quarterly; a 30D window would be empty most of the year |
