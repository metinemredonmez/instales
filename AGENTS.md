# AGENTS.md — InstiLens

Project-specific guidance for coding agents. (The Prime Lab `AGENTS.md` in the home directory is
about verifiers environments and does not apply to this repository.)

## Non-negotiable laws
1. **Never distribute an aggregate across funds.** A GROUPED event keeps `allocated_nominal = NULL`
   for every related fund. Splitting evenly is a bug, not an approximation.
2. **Every fact row carries lineage**: `disclosure_id` + `confidence`. Do not add tables that lose it.
3. **No double counting across sources.** Snapshot diffs are canonical for the periods they cover;
   transaction events count only after the latest covering snapshot (`_event_is_uncovered`).
4. **Corrections supersede, never overwrite.** Use `Disclosure.supersedes_id`; keep old rows.
5. **Scores are deterministic and explainable.** New components go into `WEIGHTS` + `components`
   and must be documented in `docs/04-confidence-and-scoring.md`. No ML in the score path.
6. **The AI layer never invents numbers.** New model capabilities are added as tools in
   `ai/tools.py`; the model reads our tables, it does not compute.
7. **Product language is descriptive, not prescriptive** ("12 funds increased" — never "buy").

8. **Schema changes go through Alembic** (`uv run alembic revision --autogenerate -m ...`); never edit
   `migrations/versions` by hand for data, and keep `render_as_batch` (SQLite dev).
9. **Auth is mandatory on data routes.** New routers go on `dependencies=[Depends(current_user)]`.

## Working rules
- Frontend: `cd frontend && npm run build` must pass (`tsc` + Vite). Charts: never dual-axis; one series per panel.
- Backend: `cd backend && uv sync && uv run pytest -q && uv run ruff check src tests`.
- Keep tests network-free; the Claude engine is exercised through its tools, not live calls.
- Fixtures under `backend/fixtures` are synthetic; keep the narrative the tests assert.
- Money is `Decimal`, quantities are `int`; never `float` in the domain or engines.
- Enums from `domain/enums.py` only; DB values are strings — compare with `==`, never `is`.
