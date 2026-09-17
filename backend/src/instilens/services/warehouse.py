"""DuckDB warehouse: one self-contained analytical copy of the data layer.

`build()` writes `releases_dir/warehouse/instilens-<YYYYMMDD>.duckdb` (and refreshes `latest.duckdb`): every
fact and reference table the pipeline fills (TABLES — never users, auth, portfolios, organizations, subscriptions,
notifications or settings), copied row for row through SQLAlchemy → pandas → DuckDB in CHUNK_ROWS slices so a
market_prices table of millions of bars never sits in memory at once; the gold views the product's read models are
built from, written as DuckDB SQL over the copied tables (v_ownership_latest, v_flows_30d, v_flows_90d,
v_fund_latest_books — same laws as services/ownership and analytics._window_activity, documented in
docs/08-warehouse.md); and a one-row `_meta` table (built_at, git rev, schema version, row counts). Types: integers
→ BIGINT, Numeric → DOUBLE (the operational store keeps Decimal; the warehouse is for analysis, not accounting),
Date → DATE, DateTime → TIMESTAMP (UTC, naive), JSON → JSON (the serialised document), Boolean → BOOLEAN,
everything else VARCHAR. `disclosures` is copied without its raw payload — the lineage columns every fact row
points at are there, the verbatim filing stays in the operational database.

The file is written under a partial name, verified by re-opening it read-only and counting every table and view,
and only then renamed into place; the newest KEEP_BUILDS dated files are kept. The build lock is a row under the
reserved `_warehouse_lock` key of app_settings — the same unique-row pattern as the pipeline lock in api/hardening,
on the same counter store, so an admin click, the weekly job and the CLI never write the same file twice at once and
a build that died is taken over after STALE_AFTER. The last outcome lives under `_warehouse_status`.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import time
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import duckdb
import pandas as pd
import sqlalchemy as sa
from sqlalchemy import inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from instilens.config import BACKEND_ROOT, settings
from instilens.domain.models import AppSetting, Base
from instilens.services import runtime_settings

log = logging.getLogger("instilens.warehouse")

SCHEMA_VERSION = 1  # bump when a table, a column mapping or a view definition changes
CHUNK_ROWS = 20_000
KEEP_BUILDS = 4
STALE_AFTER = timedelta(hours=2)  # a lock this old with no finish is a dead worker: take it over
LOCK_KEY = "_warehouse_lock"
STATUS_KEY = "_warehouse_status"
BUILD_TZ = ZoneInfo("Europe/Istanbul")  # the file name carries the scheduler's day, not the UTC one
LATEST = "latest.duckdb"
BUILD_NAME = re.compile(r"^instilens-(\d{8})\.duckdb$")
DOWNLOADABLE = re.compile(r"^(instilens-\d{8}|latest)\.duckdb$")

# Reference tables first, then the facts in the order the pipeline fills them. Everything else in the schema is
# user, auth, billing or process state and never leaves the operational database.
TABLES: tuple[str, ...] = (
    "markets", "instruments", "institutions", "funds", "disclosures",
    "portfolio_snapshots", "snapshot_holdings", "position_changes",
    "transaction_events", "transaction_event_funds",
    "market_prices", "fundamental_snapshots", "insider_transactions", "sec_filings",
    "signals", "signal_outcomes", "scores",
)
EXCLUDED_COLUMNS: dict[str, frozenset[str]] = {"disclosures": frozenset({"payload", "parse_error"})}

# --------------------------------------------------------------------------- gold views (DuckDB SQL)

# services/ownership._holder_rows: each fund's newest snapshot on or before the reference date (the latest compute
# day, today before the first compute), one row per fund × instrument, quantity > 0, with the position change that
# ends at that snapshot. `is_stale` is the staleness cut the app applies (STALE_PERIODS × PERIOD_DAYS: TR 60 days,
# US 182) — the view keeps stale holders and flags them, the reader decides.
V_OWNERSHIP_LATEST = """
CREATE VIEW v_ownership_latest AS
WITH ref AS (SELECT coalesce((SELECT max(as_of) FROM scores), current_date) AS reference_date),
latest AS (
    SELECT ps.fund_id, max(ps.as_of) AS as_of
    FROM portfolio_snapshots ps, ref
    WHERE ps.as_of <= ref.reference_date
    GROUP BY ps.fund_id
)
SELECT inst.market_code,
       i.id AS instrument_id, i.symbol, i.name AS instrument_name,
       f.id AS fund_id, f.code AS fund_code, f.name AS fund_name,
       inst.id AS institution_id, inst.name AS institution,
       h.quantity, h.market_value, h.weight_pct,
       CASE WHEN i.shares_outstanding > 0 THEN h.quantity * 100.0 / i.shares_outstanding END AS pct_of_shares,
       ps.id AS snapshot_id, ps.disclosure_id, ps.as_of, ps.confidence,
       pc.activity AS last_move, pc.period_end AS last_move_period_end,
       date_diff('day', ps.as_of, ref.reference_date) > CASE WHEN inst.market_code = 'US' THEN 182 ELSE 60 END AS is_stale,
       ref.reference_date
FROM snapshot_holdings h
JOIN portfolio_snapshots ps ON ps.id = h.snapshot_id
JOIN latest ON latest.fund_id = ps.fund_id AND latest.as_of = ps.as_of
JOIN funds f ON f.id = ps.fund_id
JOIN institutions inst ON inst.id = f.institution_id
JOIN instruments i ON i.id = h.instrument_id
LEFT JOIN position_changes pc ON pc.to_snapshot_id = ps.id AND pc.instrument_id = h.instrument_id
CROSS JOIN ref
WHERE h.quantity > 0
"""

# services/ownership.fund_books: each fund's newest snapshot whatever its age, positions > 0.
V_FUND_LATEST_BOOKS = """
CREATE VIEW v_fund_latest_books AS
WITH latest AS (SELECT fund_id, max(as_of) AS as_of FROM portfolio_snapshots GROUP BY fund_id)
SELECT inst.market_code,
       f.id AS fund_id, f.code AS fund_code, f.name AS fund_name, f.fund_type,
       inst.id AS institution_id, inst.name AS institution,
       ps.id AS snapshot_id, ps.disclosure_id, ps.as_of, ps.total_value, ps.source, ps.confidence,
       i.id AS instrument_id, i.symbol, i.name AS instrument_name,
       h.quantity, h.market_value, h.weight_pct,
       CASE WHEN i.shares_outstanding > 0 THEN h.quantity * 100.0 / i.shares_outstanding END AS pct_of_shares
FROM portfolio_snapshots ps
JOIN latest ON latest.fund_id = ps.fund_id AND latest.as_of = ps.as_of
JOIN snapshot_holdings h ON h.snapshot_id = ps.id AND h.quantity > 0
JOIN funds f ON f.id = ps.fund_id
JOIN institutions inst ON inst.id = f.institution_id
JOIN instruments i ON i.id = h.instrument_id
"""

# services/analytics._window_activity + _flow_row for a {days}-day window ending on the reference date: snapshot
# diffs whose period_end falls in (window_start, as_of] plus the live transaction events the de-dup law lets through
# (an event counts only after the latest snapshot of any of its funds known on as_of). An event's value is the
# disclosed net_value, else net_nominal × the latest close on or before the trade date. Parties follow the breadth
# law — a fund from a diff or an EXACT event is one party ("fund:<id>"), a GROUPED event is its institution
# ("inst:<id>") — and funds_new / funds_exited count the diffs' NEW / EXIT plus the entries (0 % → > 0 % on a buy)
# and full exits (→ 0 % on a sell) events disclose, a GROUPED one dropped when one of its funds is already counted.
V_FLOWS = """
CREATE VIEW v_flows_{days}d AS
WITH ref AS (
    SELECT coalesce((SELECT max(as_of) FROM scores), current_date) AS as_of,
           coalesce((SELECT max(as_of) FROM scores), current_date) - {days} AS window_start
),
changes AS (
    SELECT pc.* FROM position_changes pc, ref
    WHERE pc.period_end > ref.window_start AND pc.period_end <= ref.as_of
),
latest_snap AS (
    SELECT ps.fund_id, max(ps.as_of) AS as_of FROM portfolio_snapshots ps, ref
    WHERE ps.as_of <= ref.as_of GROUP BY ps.fund_id
),
event_funds AS (
    SELECT tef.event_id, tef.fund_id, ls.as_of AS covered_to
    FROM transaction_event_funds tef LEFT JOIN latest_snap ls ON ls.fund_id = tef.fund_id
),
events AS (
    SELECT e.*, (SELECT min(ef.fund_id) FROM event_funds ef WHERE ef.event_id = e.id) AS first_fund_id
    FROM transaction_events e, ref
    WHERE NOT e.is_superseded AND e.effective_date > ref.window_start AND e.effective_date <= ref.as_of
      AND e.effective_date > coalesce((SELECT max(ef.covered_to) FROM event_funds ef WHERE ef.event_id = e.id), DATE '0001-01-01')
),
priced AS (
    SELECT e.*,
           coalesce(e.net_value, e.net_nominal * (SELECT mp.close FROM market_prices mp
                                                  WHERE mp.instrument_id = e.instrument_id AND mp.trade_date <= e.effective_date
                                                  ORDER BY mp.trade_date DESC LIMIT 1)) AS value,
           e.confidence = 'EXACT' AND e.first_fund_id IS NOT NULL AS exact_fund,
           CASE WHEN e.confidence = 'EXACT' AND e.first_fund_id IS NOT NULL THEN 'fund:' || CAST(e.first_fund_id AS VARCHAR)
                ELSE 'inst:' || CAST(e.institution_id AS VARCHAR) END AS party,
           CASE WHEN e.net_nominal > 0 AND coalesce(e.ownership_before_pct, 0) = 0 AND e.ownership_after_pct > 0 THEN 'NEW'
                WHEN e.net_nominal < 0 AND e.ownership_after_pct = 0 THEN 'EXIT' END AS entry_exit
    FROM events e
),
snapshot_parties AS (SELECT instrument_id, 'fund:' || CAST(fund_id AS VARCHAR) AS party, activity FROM changes),
exact_moves AS (SELECT instrument_id, party, entry_exit AS activity FROM priced WHERE entry_exit IS NOT NULL AND exact_fund),
counted AS (
    SELECT instrument_id, party, activity FROM snapshot_parties WHERE activity IN ('NEW', 'EXIT')
    UNION SELECT instrument_id, party, activity FROM exact_moves
),
grouped_moves AS (
    SELECT p.instrument_id, p.party, p.entry_exit AS activity
    FROM priced p
    WHERE p.entry_exit IS NOT NULL AND NOT p.exact_fund
      AND NOT EXISTS (SELECT 1 FROM event_funds ef JOIN counted c
                      ON c.party = 'fund:' || CAST(ef.fund_id AS VARCHAR) AND c.activity = p.entry_exit AND c.instrument_id = p.instrument_id
                      WHERE ef.event_id = p.id)
),
all_moves AS (
    SELECT instrument_id, party, activity FROM snapshot_parties
    UNION ALL SELECT instrument_id, party, activity FROM exact_moves
    UNION ALL SELECT instrument_id, party, activity FROM grouped_moves
),
inc_parties AS (
    SELECT instrument_id, party FROM snapshot_parties WHERE activity IN ('ADD', 'NEW')
    UNION SELECT instrument_id, party FROM priced WHERE net_nominal > 0
),
red_parties AS (
    SELECT instrument_id, party FROM snapshot_parties WHERE activity IN ('REDUCE', 'EXIT')
    UNION SELECT instrument_id, party FROM priced WHERE net_nominal < 0
),
change_flows AS (SELECT instrument_id, sum(delta_value) AS flow, sum(delta_qty) AS qty, count(*) AS n FROM changes GROUP BY instrument_id),
event_flows AS (SELECT instrument_id, sum(value) AS flow, sum(net_nominal) AS qty, count(*) AS n FROM priced GROUP BY instrument_id),
universe AS (SELECT instrument_id FROM changes UNION SELECT instrument_id FROM priced)
SELECT i.market_code, i.id AS instrument_id, i.symbol, i.name,
       coalesce(cf.flow, 0) + coalesce(ef.flow, 0) AS net_flow_value,
       coalesce(cf.qty, 0) + coalesce(ef.qty, 0) AS net_qty,
       (SELECT count(*) FROM inc_parties x WHERE x.instrument_id = u.instrument_id) AS funds_increasing,
       (SELECT count(*) FROM red_parties x WHERE x.instrument_id = u.instrument_id) AS funds_reducing,
       (SELECT count(DISTINCT party) FROM all_moves x WHERE x.instrument_id = u.instrument_id AND x.activity = 'NEW') AS funds_new,
       (SELECT count(DISTINCT party) FROM all_moves x WHERE x.instrument_id = u.instrument_id AND x.activity = 'EXIT') AS funds_exited,
       coalesce(cf.n, 0) AS position_changes, coalesce(ef.n, 0) AS transaction_events,
       ref.window_start, ref.as_of
FROM universe u
JOIN instruments i ON i.id = u.instrument_id
LEFT JOIN change_flows cf ON cf.instrument_id = u.instrument_id
LEFT JOIN event_flows ef ON ef.instrument_id = u.instrument_id
CROSS JOIN ref
"""

VIEWS: dict[str, str] = {
    "v_ownership_latest": V_OWNERSHIP_LATEST,
    "v_flows_30d": V_FLOWS.format(days=30),
    "v_flows_90d": V_FLOWS.format(days=90),
    "v_fund_latest_books": V_FUND_LATEST_BOOKS,
}


class WarehouseError(RuntimeError):
    pass


def warehouse_dir() -> Path:
    return Path(settings.releases_dir) / "warehouse"


# --------------------------------------------------------------------------- copying tables


def duck_type(column: sa.Column) -> str:
    """The DuckDB column type a SQLAlchemy column lands in (see the module docstring)."""
    t = column.type
    if isinstance(t, sa.Boolean):
        return "BOOLEAN"
    if isinstance(t, sa.Integer):  # BigInteger / SmallInteger subclass it
        return "BIGINT"
    if isinstance(t, sa.Numeric):  # Float subclasses it too
        return "DOUBLE"
    if isinstance(t, sa.DateTime):
        return "TIMESTAMP"
    if isinstance(t, sa.Date):
        return "DATE"
    if isinstance(t, sa.JSON):
        return "JSON"
    return "VARCHAR"


def table_columns(name: str) -> list[tuple[str, str]]:
    """(column, DuckDB type) of a copied table, in schema order, minus EXCLUDED_COLUMNS."""
    table = Base.metadata.tables[name]
    skip = EXCLUDED_COLUMNS.get(name, frozenset())
    return [(c.name, duck_type(c)) for c in table.columns if c.name not in skip]


def _frame(rows: list, columns: list[tuple[str, str]]) -> pd.DataFrame:
    """One chunk of rows as a DataFrame whose dtypes DuckDB reads without guessing: nullable Int64 / Float64 /
    boolean for the numeric kinds, the JSON document serialised, dates and timestamps as Python objects (DuckDB
    types them; an all-NULL column casts cleanly in the INSERT)."""
    cols = list(zip(*rows, strict=True)) if rows else [() for _ in columns]
    data: dict[str, Any] = {}
    for (name, duck), values in zip(columns, cols, strict=True):
        if duck == "DOUBLE":
            data[name] = pd.array([float(v) if v is not None else None for v in values], dtype="Float64")
        elif duck == "BIGINT":
            data[name] = pd.array(list(values), dtype="Int64")
        elif duck == "BOOLEAN":
            data[name] = pd.array(list(values), dtype="boolean")
        elif duck == "JSON":
            data[name] = pd.Series([json.dumps(v, ensure_ascii=False, separators=(",", ":")) if v is not None else None for v in values], dtype=object)
        else:
            data[name] = pd.Series(list(values), dtype=object)
    return pd.DataFrame(data)


def copy_table(session: Session, con: duckdb.DuckDBPyConnection, name: str, chunk_rows: int = CHUNK_ROWS) -> int:
    """Create `name` in the DuckDB file and stream the source rows into it in primary-key order, `chunk_rows` at a
    time (SQLAlchemy yield_per — the driver fetches in slices, the DataFrame is one slice). Returns the rows written."""
    table = Base.metadata.tables[name]
    columns = table_columns(name)
    con.execute(f'CREATE TABLE "{name}" ({", ".join(f"{_q(c)} {t}" for c, t in columns)})')
    stmt = select(*[table.c[c] for c, _ in columns]).order_by(*table.primary_key.columns).execution_options(yield_per=chunk_rows)
    cast = ", ".join(f"CAST({_q(c)} AS {t}) AS {_q(c)}" for c, t in columns)
    written = 0
    for rows in session.execute(stmt).partitions(chunk_rows):
        frame = _frame(list(rows), columns)
        con.register("chunk", frame)
        con.execute(f'INSERT INTO "{name}" SELECT {cast} FROM chunk')
        con.unregister("chunk")
        written += len(frame)
    return written


def _q(name: str) -> str:
    return f'"{name}"'


# --------------------------------------------------------------------------- the build


def git_rev() -> str | None:
    """HEAD of the checkout the code runs from; None in a container without .git or git."""
    try:
        out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=BACKEND_ROOT, capture_output=True, text=True, timeout=5, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    rev = out.stdout.strip()
    return rev if out.returncode == 0 and re.fullmatch(r"[0-9a-f]{40}", rev) else None


def _alembic_revision(session: Session) -> str | None:
    # Inspected on the session's own connection: a second checkout would reset (roll back) a shared in-memory
    # SQLite connection under the caller's feet, and Postgres would abort the transaction on a missing table.
    if not inspect(session.connection()).has_table("alembic_version"):  # tests create the schema directly
        return None
    return session.execute(text("SELECT version_num FROM alembic_version")).scalar()


def _app_version() -> str | None:
    try:
        return version("instilens")
    except PackageNotFoundError:
        return None


def build(session: Session, *, now: datetime | None = None, chunk_rows: int = CHUNK_ROWS) -> dict:
    """Write the dated file, verify it, refresh latest.duckdb, prune to KEEP_BUILDS. Returns the build's record
    (`build_json` shape plus `seconds`). Raises WarehouseError when the verification finds a row count off."""
    started = time.monotonic()
    now = now or datetime.now(UTC)
    folder = warehouse_dir()
    folder.mkdir(parents=True, exist_ok=True)
    name = f"instilens-{now.astimezone(BUILD_TZ):%Y%m%d}.duckdb"
    final = folder / name
    partial = folder / f".{name}.{os.getpid()}.partial"
    _unlink(partial)
    counts: dict[str, int] = {}
    try:
        con = duckdb.connect(str(partial))
        try:
            for table in TABLES:
                t0 = time.monotonic()
                counts[table] = copy_table(session, con, table, chunk_rows)
                log.info("warehouse %s: %s rows in %.1fs", table, counts[table], time.monotonic() - t0)
            for ddl in VIEWS.values():
                con.execute(ddl)
            con.execute("CREATE TABLE _meta (built_at TIMESTAMP, git_rev VARCHAR, schema_version INTEGER, app_version VARCHAR, "
                        "alembic_revision VARCHAR, source_dialect VARCHAR, row_counts JSON)")
            con.execute("INSERT INTO _meta VALUES (?, ?, ?, ?, ?, ?, ?)",
                        [now.astimezone(UTC).replace(tzinfo=None), git_rev(), SCHEMA_VERSION, _app_version(), _alembic_revision(session),
                         session.get_bind().dialect.name, json.dumps(counts)])
        finally:
            con.close()
        verify(partial, counts)
        os.replace(partial, final)
    except BaseException:
        _unlink(partial)
        raise
    _refresh_latest(final)
    removed = prune(folder)
    seconds = round(time.monotonic() - started, 1)
    log.info("warehouse %s written: %s rows, %s bytes, %.1fs, pruned %s", name, sum(counts.values()), final.stat().st_size, seconds, removed)
    return {**build_json(final), "seconds": seconds, "pruned": removed}


def verify(path: Path, expected: dict[str, int]) -> None:
    """Re-open the file read-only and count every table against what was written; run every view once so a
    definition that no longer binds to the copied schema fails the build, not the first reader."""
    con = duckdb.connect(str(path), read_only=True)
    try:
        for table, n in expected.items():
            found = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
            if found != n:
                raise WarehouseError(f"{path.name}: {table} has {found} rows, {n} were written")
        for view in VIEWS:
            con.execute(f'SELECT count(*) FROM "{view}"').fetchone()
        if con.execute("SELECT count(*) FROM _meta").fetchone()[0] != 1:
            raise WarehouseError(f"{path.name}: _meta is not one row")
    finally:
        con.close()


def _refresh_latest(final: Path) -> None:
    """latest.duckdb = a copy of the newest build, swapped in atomically (a download in flight keeps its inode)."""
    tmp = final.parent / f".{LATEST}.{os.getpid()}.partial"
    shutil.copyfile(final, tmp)
    os.replace(tmp, final.parent / LATEST)


def prune(folder: Path, keep: int = KEEP_BUILDS) -> int:
    """Delete dated builds beyond the newest `keep` (by the date in the name); latest.duckdb is never touched."""
    dated = sorted((p for p in folder.iterdir() if BUILD_NAME.match(p.name)), key=lambda p: p.name, reverse=True)
    removed = 0
    for old in dated[keep:]:
        old.unlink(missing_ok=True)
        removed += 1
    return removed


def _unlink(path: Path) -> None:
    path.unlink(missing_ok=True)
    Path(f"{path}.wal").unlink(missing_ok=True)


# --------------------------------------------------------------------------- listing


def read_meta(path: Path) -> dict:
    con = duckdb.connect(str(path), read_only=True)
    try:
        built_at, rev, schema, app, alembic, dialect, counts = con.execute(
            "SELECT built_at, git_rev, schema_version, app_version, alembic_revision, source_dialect, row_counts FROM _meta").fetchone()
    finally:
        con.close()
    return {"built_at": built_at.replace(tzinfo=UTC).isoformat(), "git_rev": rev, "schema_version": schema, "app_version": app,
            "alembic_revision": alembic, "source_dialect": dialect, "row_counts": json.loads(counts)}


def build_json(path: Path) -> dict:
    """One build for the admin card: `rows` is the total, `row_counts` the per-table breakdown, `url` the admin
    download (bearer-authenticated, so the UI fetches it rather than linking). A file whose _meta cannot be read
    (a foreign or damaged file dropped into the folder) is listed with `error` rather than hiding the healthy builds."""
    out: dict[str, Any] = {"name": path.name, "size": path.stat().st_size, "url": f"{settings.public_url}/api/v1/admin/warehouse/download/{path.name}",
                           "built_at": None, "rows": None, "row_counts": None, "git_rev": None, "schema_version": None, "error": None}
    try:
        meta = read_meta(path)
    except Exception as exc:  # noqa: BLE001 — duckdb raises its own hierarchy; the listing must survive one bad file
        out["error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        return out
    out.update(meta, rows=sum(meta["row_counts"].values()))
    return out


def list_builds() -> list[dict]:
    """Dated builds, newest first."""
    folder = warehouse_dir()
    if not folder.is_dir():
        return []
    return [build_json(p) for p in sorted((p for p in folder.iterdir() if BUILD_NAME.match(p.name)), key=lambda p: p.name, reverse=True)]


def latest_json() -> dict | None:
    path = warehouse_dir() / LATEST
    return build_json(path) if path.is_file() else None


def download_path(name: str) -> Path | None:
    """The file behind an admin download: a dated build or latest.duckdb, inside the warehouse folder only.
    None for any other name (traversal attempts included) or a missing file."""
    if not DOWNLOADABLE.match(name):
        return None
    folder = warehouse_dir().resolve()
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        return None
    return path


# --------------------------------------------------------------------------- lock and status


def _store() -> Session:
    from instilens.api import hardening

    return hardening._store()  # the counter store: the app engine in production, a throwaway one under tests


def acquire_lock(started_by: str | None) -> bool:
    """Insert the row under LOCK_KEY; False when another build holds it (a stale one is taken over first)."""
    now = datetime.now(UTC)
    with _store() as s:
        stale = s.scalar(select(AppSetting).where(AppSetting.key == LOCK_KEY, AppSetting.updated_at < now - STALE_AFTER))
        if stale is not None:
            log.warning("warehouse lock held since %s by %s never finished; taking it over", stale.updated_at, stale.updated_by)
            s.delete(stale)
            s.commit()
        s.add(AppSetting(key=LOCK_KEY, value={"v": {"started_by": started_by, "started_at": now.isoformat()}}, updated_at=now, updated_by=started_by))
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            return False
        return True


def held_lock() -> dict | None:
    """The lock row's `{started_by, started_at}` while a build holds it, else None."""
    with _store() as s:
        return runtime_settings.load_json(s, LOCK_KEY)


def release_lock(started_by: str | None, *, started_at: str | None, result: dict | None = None, error: str | None = None) -> dict:
    """Drop the lock row — only when it is still this build's: a build that outlived STALE_AFTER was taken over by
    the next one (`acquire_lock`), and dropping that holder's row would let a third build start while the second is
    still writing the same dated file. The holder is `started_by` plus, when the caller knows it, the `started_at`
    the row was written with. The outcome is recorded under STATUS_KEY either way (what the admin card shows as
    the last build)."""
    last = {"started_by": started_by, "started_at": started_at, "finished_at": datetime.now(UTC).isoformat(),
            "name": result["name"] if result else None, "rows": result["rows"] if result else None,
            "seconds": result["seconds"] if result else None, "error": error}
    with _store() as s:
        row = s.get(AppSetting, LOCK_KEY)
        if row is not None:
            holder = (row.value or {}).get("v") or {}
            mine = holder.get("started_by") == started_by and (started_at is None or holder.get("started_at") == started_at)
            if mine:
                s.delete(row)
            else:
                log.warning("warehouse lock now held by %s since %s; build of %s (%s) leaves it in place", holder.get("started_by"), holder.get("started_at"), started_by, started_at)
        runtime_settings.store_json(s, STATUS_KEY, last, started_by or "warehouse")
        s.commit()
    return last


def status() -> dict:
    """{running, started_by, started_at, last} — the lock row while a build runs, the last recorded outcome."""
    with _store() as s:
        lock = runtime_settings.load_json(s, LOCK_KEY)
        last = runtime_settings.load_json(s, STATUS_KEY)
    return {"running": lock is not None, "started_by": lock.get("started_by") if lock else None,
            "started_at": lock.get("started_at") if lock else None, "last": last}


def run_build(started_by: str | None, *, session_factory=None) -> dict:
    """The build under a lock the caller already holds (`acquire_lock`): its own session (`session_factory` is a
    context manager yielding one — db.session.session_scope unless a test hands in its own), every outcome recorded,
    the lock released when it is still this build's (its `started_at` is the lock row's, so a takeover after
    STALE_AFTER is told apart). Returns the status record (`error` set when the build failed)."""
    import traceback

    from instilens.db.session import session_scope

    lock = held_lock()
    started_at = lock["started_at"] if lock and lock.get("started_by") == started_by and lock.get("started_at") else datetime.now(UTC).isoformat()
    try:
        with (session_factory or session_scope)() as s:
            result = build(s)
    except Exception:
        log.exception("warehouse build failed")
        return release_lock(started_by, started_at=started_at, error=traceback.format_exc()[-2000:])
    return release_lock(started_by, started_at=started_at, result=result)


def build_locked(started_by: str | None, *, session_factory=None) -> dict | None:
    """Acquire the lock and build in this thread (the scheduler, the CLI); None when a build is already running."""
    if not acquire_lock(started_by):
        return None
    return run_build(started_by, session_factory=session_factory)
