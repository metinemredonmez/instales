"""DuckDB warehouse (services/warehouse): the chunked table copy and its type mapping, the gold views against the
Python read models they mirror, _meta, latest.duckdb and the four-build retention, the build lock, the scheduler
gate, the CLI and the admin routes (403 for non-admins, the list, the guarded download). Network-free; the source
is the synthetic KAP chain plus the real Form 4 fixtures behind the EDGAR mock of test_insiders."""

from __future__ import annotations

import contextlib
from datetime import UTC, datetime, timedelta

import duckdb
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from instilens.config import settings
from instilens.domain.models import AppSetting, Base, Fund, Instrument
from instilens.services import analytics, auth, insiders, ownership, runtime_settings, warehouse
from tests.conftest import AS_OF
from tests.test_insiders import AS_OF as SEC_AS_OF
from tests.test_insiders import _Edgar, _us


@pytest.fixture
def releases_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "releases_dir", tmp_path)
    return tmp_path


def _with_form4(session) -> None:
    """Real Form 4 rows next to the KAP fixture chain, so insider_transactions and sec_filings are not empty."""
    _us(session, "AAPL", "OXY")
    insiders.refresh(session, client=_Edgar().client, as_of=SEC_AS_OF, pause_s=0)
    session.commit()


def _counts(session) -> dict[str, int]:
    return {t: session.execute(select(func.count()).select_from(Base.metadata.tables[t])).scalar() for t in warehouse.TABLES}


def _open(path):
    return duckdb.connect(str(path), read_only=True)


# --- the copy ---------------------------------------------------------------------------------------


def test_type_mapping_and_excluded_columns():
    col = lambda t: sa.Column("x", t)  # noqa: E731
    assert [warehouse.duck_type(col(t)) for t in (sa.Boolean(), sa.Integer(), sa.BigInteger(), sa.Numeric(20, 4), sa.Float(), sa.DateTime(), sa.Date(), sa.JSON(), sa.String(8), sa.Text())] == \
        ["BOOLEAN", "BIGINT", "BIGINT", "DOUBLE", "DOUBLE", "TIMESTAMP", "DATE", "JSON", "VARCHAR", "VARCHAR"]
    names = [c for c, _ in warehouse.table_columns("disclosures")]
    assert "payload" not in names and "parse_error" not in names and names[:4] == ["id", "market_code", "source", "source_id"]
    assert dict(warehouse.table_columns("market_prices")) == {"instrument_id": "BIGINT", "trade_date": "DATE", "open": "DOUBLE", "high": "DOUBLE", "low": "DOUBLE", "close": "DOUBLE", "volume": "BIGINT", "source": "VARCHAR"}
    # The complement is pinned exactly: a new model breaks this until it is classified as exported or excluded (docs/08 lists both).
    assert set(Base.metadata.tables) - set(warehouse.TABLES) == {
        "users", "auth_tokens", "waitlist", "watchlists", "watchlist_items", "alert_rules", "notifications", "live_events", "brief_deliveries", "push_subscriptions",
        "portfolios", "portfolio_positions", "portfolio_transactions", "organizations", "org_members", "subscriptions", "processed_webhooks",
        "app_settings", "audit_events", "rate_hits", "pipeline_runs", "releases", "release_files", "news_items", "news_rules", "ai_notes", "searchable_texts", "fundamentals",
    }
    assert set(warehouse.TABLES) <= set(Base.metadata.tables)


def test_build_copies_every_table_in_chunks_and_verifies(session, pipeline_run, releases_dir):
    _with_form4(session)
    expected = _counts(session)
    assert expected["market_prices"] == 36 and expected["insider_transactions"] == 5 and expected["sec_filings"] == 8 and expected["fundamental_snapshots"] == 0
    built = datetime(2026, 9, 17, 22, 30, tzinfo=UTC)  # 01:30 Istanbul on the 18th: the file carries the scheduler's day
    out = warehouse.build(session, now=built, chunk_rows=7)  # several partitions per table, an empty last one nowhere
    folder = releases_dir / "warehouse"
    assert out["name"] == "instilens-20260918.duckdb" and out["row_counts"] == expected and out["rows"] == sum(expected.values()) and out["error"] is None
    assert out["url"].endswith("/api/v1/admin/warehouse/download/instilens-20260918.duckdb") and out["pruned"] == 0 and out["seconds"] >= 0
    assert sorted(p.name for p in folder.iterdir()) == ["instilens-20260918.duckdb", "latest.duckdb"]  # no partial or .wal left behind
    assert (folder / "latest.duckdb").read_bytes() == (folder / out["name"]).read_bytes()

    con = _open(folder / out["name"])
    try:
        kinds = dict(con.execute("SELECT table_name, table_type FROM information_schema.tables").fetchall())
        assert {k for k, v in kinds.items() if v == "BASE TABLE"} == set(warehouse.TABLES) | {"_meta"}
        assert {k for k, v in kinds.items() if v == "VIEW"} == set(warehouse.VIEWS) == {"v_ownership_latest", "v_flows_30d", "v_flows_90d", "v_fund_latest_books"}
        for table, n in expected.items():
            assert con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0] == n, table
        # Types as documented: Decimal → DOUBLE, dates → DATE, timestamps naive UTC, JSON documents queryable, bools kept.
        types = dict(con.execute("SELECT column_name, data_type FROM information_schema.columns WHERE table_name = 'transaction_events'").fetchall())
        assert types["avg_price"] == "DOUBLE" and types["effective_date"] == "DATE" and types["published_at"] == "TIMESTAMP" and types["is_superseded"] == "BOOLEAN" and types["buy_nominal"] == "BIGINT"
        assert con.execute("SELECT data_type FROM information_schema.columns WHERE table_name = 'signals' AND column_name = 'evidence'").fetchone()[0] == "JSON"
        assert con.execute("SELECT max(CAST(evidence->>'$.consecutive_periods' AS INTEGER)) FROM signals WHERE signal_type = 'ACCUMULATION'").fetchone()[0] == 3
        assert "payload" not in [r[0] for r in con.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'disclosures'").fetchall()]
        close = con.execute("SELECT close FROM market_prices WHERE instrument_id = (SELECT id FROM instruments WHERE symbol = 'THYAO') AND trade_date <= ? ORDER BY trade_date DESC LIMIT 1", [AS_OF]).fetchone()[0]
        assert isinstance(close, float) and close == float(analytics.close_on_or_before(session, session.scalar(select(Instrument.id).where(Instrument.symbol == "THYAO")), AS_OF))
        # Form 4 rows keep their lineage: every insider row points at a disclosure that is in the file.
        assert con.execute("SELECT count(*) FROM insider_transactions t LEFT JOIN disclosures d ON d.id = t.disclosure_id WHERE d.id IS NULL").fetchone()[0] == 0
        assert con.execute("SELECT count(*) FROM insider_transactions WHERE price IS NULL").fetchone()[0] == 2  # the two RSU settlement rows filed without a price stay NULL, not 0
        assert con.execute("SELECT items FROM sec_filings WHERE form = '8-K' AND items IS NOT NULL LIMIT 1").fetchone()[0] == '["2.02","9.01"]'
        meta = con.execute("SELECT * FROM _meta").fetchall()
        assert len(meta) == 1 and meta[0][0] == datetime(2026, 9, 17, 22, 30) and meta[0][2] == warehouse.SCHEMA_VERSION and meta[0][3] == "0.1.0" and meta[0][5] == "sqlite"
    finally:
        con.close()
    listed = warehouse.list_builds()
    assert [b["name"] for b in listed] == [out["name"]] and listed[0]["built_at"] == "2026-09-17T22:30:00+00:00" and listed[0]["rows"] == out["rows"] and listed[0]["size"] > 0
    assert (listed[0]["git_rev"] is None or len(listed[0]["git_rev"]) == 40) and listed[0]["alembic_revision"] is None  # create_all schema: no alembic_version table
    assert warehouse.latest_json()["built_at"] == listed[0]["built_at"] and warehouse.latest_json()["name"] == "latest.duckdb"


def test_verification_rejects_a_short_file_and_leaves_no_partial(session, pipeline_run, releases_dir, monkeypatch):
    real = warehouse.copy_table

    def short(s, con, name, chunk_rows=warehouse.CHUNK_ROWS):
        n = real(s, con, name, chunk_rows)
        return n + 1 if name == "scores" else n  # claims one row more than it wrote

    monkeypatch.setattr(warehouse, "copy_table", short)
    with pytest.raises(warehouse.WarehouseError, match="scores has 26 rows, 27 were written"):
        warehouse.build(session)
    assert not (releases_dir / "warehouse").exists() or list((releases_dir / "warehouse").iterdir()) == []


# --- the gold views -----------------------------------------------------------------------------------


def _flow_rows(con, view: str) -> dict[str, tuple]:
    return {r[0]: r[1:] for r in con.execute(f"SELECT symbol, net_flow_value, net_qty, funds_increasing, funds_reducing, funds_new, funds_exited FROM {view}").fetchall()}


def test_views_reproduce_the_read_models(session, pipeline_run, releases_dir):
    out = warehouse.build(session)
    con = _open(releases_dir / "warehouse" / out["name"])
    try:
        # Flows: the same rows, values and breadth-law party counts as /flows for 30 and 90 days (EXACT and GROUPED
        # events, a superseded one, an institution-level entry and the de-dup law are all in the fixture chain).
        for days in (30, 90):
            py = analytics.window_flows(session, "TR", days)
            rows = _flow_rows(con, f"v_flows_{days}d")
            for r in py["accumulated"] + py["distributed"]:
                assert rows[r["symbol"]] == (r["net_flow_value"], r["net_qty"], r["funds_increasing"], r["funds_reducing"], r["funds_new"], r["funds_exited"]), (days, r["symbol"])
            assert {s for s, v in rows.items() if v[0] != 0} == {r["symbol"] for r in py["accumulated"] + py["distributed"]}  # a zero-net row is in neither leaderboard
            start, as_of = con.execute(f"SELECT DISTINCT window_start, as_of FROM v_flows_{days}d").fetchone()
            assert (start.isoformat(), as_of.isoformat()) == (py["window_start"], py["as_of"])
        # Ownership: the holders of a fixture symbol as /stocks/THYAO/ownership lists them, with the move behind each.
        own = ownership.stock_ownership(session, "TR", "THYAO")
        holders = con.execute("SELECT fund_code, quantity, market_value, weight_pct, as_of, last_move, last_move_period_end, confidence, is_stale, reference_date "
                              "FROM v_ownership_latest WHERE symbol = 'THYAO' ORDER BY quantity DESC, fund_code").fetchall()
        assert [(h["fund"], h["quantity"], h["market_value"], h["weight_pct"], h["as_of"], h["last_move"], h["last_move_period_end"], h["confidence"]) for h in own["holders"]] == \
            [(f, q, mv, w, d.isoformat(), m, pe.isoformat() if pe else None, c) for f, q, mv, w, d, m, pe, c, _, _ in holders]
        assert all(stale is False and ref == ownership.reference_date(session) for *_, stale, ref in holders) and own["stale_holders"] == 0
        assert con.execute("SELECT count(DISTINCT fund_id) FROM v_ownership_latest WHERE symbol = 'THYAO'").fetchone()[0] == len(holders)  # one row per fund
        assert con.execute("SELECT count(*) FROM v_ownership_latest WHERE disclosure_id IS NULL").fetchone()[0] == 0  # lineage travels with the view
        # Fund books: every fund's newest snapshot, positions > 0, the weights the report stated.
        funds = session.scalars(select(Fund).order_by(Fund.code)).all()
        books = ownership.fund_books(session, funds)
        for f in funds:
            rows = con.execute("SELECT symbol, quantity, weight_pct, as_of FROM v_fund_latest_books WHERE fund_code = ? ORDER BY symbol", [f.code]).fetchall()
            assert [(s, q, w) for s, q, w, _ in rows] == [(p.symbol, p.quantity, float(p.weight_pct) if p.weight_pct is not None else None) for p in sorted(books[f.code].holdings.values(), key=lambda p: p.symbol)]
            assert all(d == books[f.code].as_of for *_, d in rows)
    finally:
        con.close()


# --- retention, latest, downloads ---------------------------------------------------------------------


def test_prune_keeps_the_newest_four_dated_builds(session, pipeline_run, releases_dir):
    folder = releases_dir / "warehouse"
    names = []
    for day in range(1, 7):
        names.append(warehouse.build(session, now=datetime(2026, 9, day, 12, tzinfo=UTC))["name"])
    (folder / "notes.txt").write_text("not a build")
    assert names == [f"instilens-202609{d:02d}.duckdb" for d in range(1, 7)]
    assert sorted(p.name for p in folder.iterdir()) == names[2:] + ["latest.duckdb", "notes.txt"]  # 4 kept, foreign files untouched
    assert (folder / "latest.duckdb").read_bytes() == (folder / names[-1]).read_bytes()
    assert [b["name"] for b in warehouse.list_builds()] == names[:1:-1]  # newest first
    assert warehouse.prune(folder) == 0 and warehouse.prune(folder, keep=1) == 3 and sorted(p.name for p in folder.iterdir()) == [names[-1], "latest.duckdb", "notes.txt"]
    (folder / "instilens-20260101.duckdb").write_bytes(b"garbage")
    bad = next(b for b in warehouse.list_builds() if b["name"] == "instilens-20260101.duckdb")
    assert bad["error"] and bad["rows"] is None and bad["size"] == 7  # listed with its error, never hides the healthy builds


def test_download_path_resolves_only_inside_the_folder(session, pipeline_run, releases_dir):
    out = warehouse.build(session)
    folder = releases_dir / "warehouse"
    assert warehouse.download_path(out["name"]) == (folder / out["name"]).resolve()
    assert warehouse.download_path("latest.duckdb") == (folder / "latest.duckdb").resolve()
    (releases_dir / "0.2.0").mkdir()
    (releases_dir / "0.2.0" / "instilens-20260101.duckdb").write_bytes(b"x")
    for name in ("..", "../0.2.0/instilens-20260101.duckdb", "instilens-20260101.duckdb", "latest.duckdb.bak", "instilens-2026.duckdb", "/etc/passwd", "latest.duckdb/", "notes.txt", ""):
        assert warehouse.download_path(name) is None, name


# --- lock, scheduler, CLI -------------------------------------------------------------------------------


def test_build_lock_is_exclusive_records_outcomes_and_recovers(session, pipeline_run, releases_dir, monkeypatch):
    own = lambda: contextlib.nullcontext(session)  # noqa: E731
    assert warehouse.status() == {"running": False, "started_by": None, "started_at": None, "last": None}
    assert warehouse.acquire_lock("a@example.com") is True
    assert warehouse.acquire_lock("b@example.com") is False  # held
    assert warehouse.build_locked("scheduler", session_factory=own) is None  # the weekly job skips, never queues
    st = warehouse.status()
    assert st["running"] is True and st["started_by"] == "a@example.com" and datetime.fromisoformat(st["started_at"]) and st["last"] is None
    last = warehouse.run_build("a@example.com", session_factory=own)  # the holder builds and releases
    assert last["error"] is None and last["name"].startswith("instilens-") and last["rows"] > 0 and last["started_by"] == "a@example.com"
    st = warehouse.status()
    assert st["running"] is False and st["last"] == last and datetime.fromisoformat(last["finished_at"]) >= datetime.fromisoformat(last["started_at"])
    # A failing build still releases the lock and records why.
    def broken(s):
        raise RuntimeError("disk full")

    monkeypatch.setattr(warehouse, "build", broken)
    failed = warehouse.build_locked("cli", session_factory=own)
    assert failed["name"] is None and "disk full" in failed["error"] and warehouse.status()["running"] is False and warehouse.status()["last"]["error"] == failed["error"]
    # A worker that died mid-build leaves the row; it is taken over after STALE_AFTER.
    assert warehouse.acquire_lock("c@example.com") is True
    with warehouse._store() as s:
        s.get(AppSetting, warehouse.LOCK_KEY).updated_at = datetime.now(UTC) - warehouse.STALE_AFTER - timedelta(minutes=1)
        s.commit()
    assert warehouse.acquire_lock("d@example.com") is True and warehouse.status()["started_by"] == "d@example.com"
    # The taken-over build (c) was only slow, not dead: when it finishes, it must not release d's lock — nor may a
    # same-named holder with another started_at (its own row is gone; the stamp tells the two apart).
    c_last = warehouse.release_lock("c@example.com", started_at=None, error="finished late")
    assert c_last["error"] == "finished late" and warehouse.status()["running"] is True and warehouse.status()["started_by"] == "d@example.com"
    warehouse.release_lock("d@example.com", started_at="2026-01-01T00:00:00+00:00")
    assert warehouse.status()["running"] is True and warehouse.status()["last"]["started_by"] == "d@example.com"  # recorded, not released
    warehouse.release_lock("d@example.com", started_at=warehouse.held_lock()["started_at"])
    assert warehouse.status()["running"] is False and warehouse.held_lock() is None
    # run_build stamps its outcome with the lock row's own started_at, so the release above is exactly what it does.
    assert warehouse.acquire_lock("e@example.com") is True
    stamp = warehouse.held_lock()["started_at"]
    monkeypatch.setattr(warehouse, "build", lambda s: {"name": "instilens-20260920.duckdb", "rows": 1, "seconds": 0.0})
    assert warehouse.run_build("e@example.com", session_factory=own)["started_at"] == stamp and warehouse.status()["running"] is False
    # The lock and status rows are process state: never listed as settings, never writable through them.
    with warehouse._store() as s:
        assert all(row["key"] not in (warehouse.LOCK_KEY, warehouse.STATUS_KEY) for row in runtime_settings.snapshot(s))
        with pytest.raises(runtime_settings.SettingError):
            runtime_settings.set_many(s, {warehouse.LOCK_KEY: {"started_by": "x"}}, "x")


def test_scheduler_job_is_gated_by_the_setting(monkeypatch):
    from instilens import scheduler

    calls: list[str] = []
    monkeypatch.setattr(warehouse, "build_locked", lambda who: calls.append(who) or {"name": "instilens-20260920.duckdb", "rows": 1, "error": None})
    monkeypatch.setattr(settings, "warehouse_enabled", False)
    scheduler.warehouse_weekly()
    assert calls == []
    monkeypatch.setattr(settings, "warehouse_enabled", True)
    scheduler.warehouse_weekly()
    assert calls == ["scheduler"]
    assert runtime_settings.EDITABLE["warehouse_enabled"] == {"type": "bool", "group": "data"} and runtime_settings.coerce("warehouse_enabled", "on") is True
    assert settings.warehouse_enabled is True and runtime_settings.defaults()["warehouse_enabled"] is False  # off until an admin turns it on


def test_cli_build_and_list(session, pipeline_run, releases_dir, monkeypatch):
    from typer.testing import CliRunner

    from instilens.cli import app

    monkeypatch.setattr("instilens.db.session.session_scope", lambda: contextlib.nullcontext(session))
    runner = CliRunner()
    r = runner.invoke(app, ["warehouse", "list"])
    assert r.exit_code == 0 and "no builds under" in r.output
    r = runner.invoke(app, ["warehouse", "build"])
    assert r.exit_code == 0 and "rows in 17 tables" in r.output, r.output
    r = runner.invoke(app, ["warehouse", "list"])
    assert r.exit_code == 0 and "instilens-" in r.output and "latest.duckdb →" in r.output
    assert warehouse.acquire_lock("admin@example.com")
    r = runner.invoke(app, ["warehouse", "build"])
    assert r.exit_code == 1 and "already running (started by admin@example.com" in r.output
    warehouse.release_lock("admin@example.com", started_at=None)


# --- admin routes -----------------------------------------------------------------------------------------


def _clients(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    admin = auth.register(session, "root@example.com", "correct-horse-1", "Root")
    admin.role = "ADMIN"
    user = auth.register(session, "u@example.com", "correct-horse-1", "U")
    session.flush()
    return TestClient(app), {"authorization": f"Bearer {auth.issue_token(admin)}"}, {"authorization": f"Bearer {auth.issue_token(user)}"}


def test_admin_routes_list_download_and_toggle(session, pipeline_run, releases_dir):
    from instilens.api.main import app

    c, admin, user = _clients(session)
    try:
        assert c.get("/api/v1/admin/warehouse").status_code == 401
        for method, path in (("get", "/api/v1/admin/warehouse"), ("post", "/api/v1/admin/warehouse/build"), ("get", "/api/v1/admin/warehouse/download/latest.duckdb")):
            assert getattr(c, method)(path, headers=user).status_code == 403, path
        body = c.get("/api/v1/admin/warehouse", headers=admin).json()
        assert body == {"enabled": False, "running": False, "started_by": None, "started_at": None, "last": None, "builds": [], "latest": None, "keep": 4}
        assert c.get("/api/v1/admin/warehouse/download/latest.duckdb", headers=admin).status_code == 404  # nothing built yet

        out = warehouse.build(session, now=datetime(2026, 9, 13, 12, tzinfo=UTC))
        r = c.get("/api/v1/admin/warehouse", headers=admin)
        assert r.status_code == 200, r.text
        body = r.json()
        (b,) = body["builds"]
        assert b["name"] == "instilens-20260913.duckdb" and b["rows"] == out["rows"] and b["size"] == out["size"] and b["built_at"] == "2026-09-13T12:00:00+00:00"
        assert b["url"] == f"{settings.public_url}/api/v1/admin/warehouse/download/instilens-20260913.duckdb" and b["row_counts"]["scores"] == 26 and b["error"] is None
        assert body["latest"]["name"] == "latest.duckdb" and body["latest"]["built_at"] == b["built_at"]
        r = c.get("/api/v1/admin/warehouse/download/instilens-20260913.duckdb", headers=admin)
        assert r.status_code == 200 and r.headers["content-type"] == "application/octet-stream" and "instilens-20260913.duckdb" in r.headers["content-disposition"]
        assert r.content == (releases_dir / "warehouse" / "instilens-20260913.duckdb").read_bytes() and len(r.content) == b["size"]
        assert c.get("/api/v1/admin/warehouse/download/latest.duckdb", headers=admin).content == r.content
        # httpx collapses a literal `..` before sending (the raw segment is covered in test_download_path_resolves_only_inside_the_folder);
        # the encoded forms reach the route as written and resolve to nothing.
        for name in ("instilens-20260101.duckdb", "latest.duckdb.bak", "%2e%2e", "%2e%2e%2f%2e%2e%2fpyproject.toml", "..%2F0.2.0%2Fx.duckdb", "instilens-20260913.duckdb%00"):
            assert c.get(f"/api/v1/admin/warehouse/download/{name}", headers=admin).status_code == 404, name

        # The weekly job's switch is a runtime setting; the card reads it back.
        r = c.put("/api/v1/admin/settings", json={"warehouse_enabled": True}, headers=admin)
        assert r.status_code == 200 and r.json()["changed"] == ["warehouse_enabled"]
        assert c.get("/api/v1/admin/warehouse", headers=admin).json()["enabled"] is True and settings.warehouse_enabled is True
        settings.warehouse_enabled = False  # what the other uvicorn worker still holds: the card re-applies the override
        assert c.get("/api/v1/admin/warehouse", headers=admin).json()["enabled"] is True and settings.warehouse_enabled is True
    finally:
        runtime_settings.set_many(session, {"warehouse_enabled": False}, "root@example.com")
        app.dependency_overrides.clear()


def test_admin_build_endpoint_uses_the_lock(session, releases_dir, monkeypatch):
    from instilens.api.main import app
    from instilens.api.routes import admin as admin_routes
    from instilens.domain.models import AuditEvent

    c, admin, _ = _clients(session)
    try:
        monkeypatch.setattr(admin_routes, "_build_warehouse_bg", lambda started_by: None)  # thread does nothing; lock stays held
        r = c.post("/api/v1/admin/warehouse/build", headers=admin).json()
        assert r["started"] is True and r["running"] is True and r["started_by"] == "root@example.com"
        r = c.post("/api/v1/admin/warehouse/build", headers=admin).json()
        assert r["started"] is False and r["running"] is True
        assert c.get("/api/v1/admin/warehouse", headers=admin).json()["running"] is True
        assert session.scalar(select(func.count(AuditEvent.id)).where(AuditEvent.kind == "admin.warehouse_build")) == 1  # the refused click is not an event
        warehouse.release_lock("root@example.com", started_at=None, error="stopped")
        body = c.get("/api/v1/admin/warehouse", headers=admin).json()
        assert body["running"] is False and body["last"]["error"] == "stopped"
        assert c.post("/api/v1/admin/warehouse/build", headers=admin).json()["started"] is True
    finally:
        app.dependency_overrides.clear()
