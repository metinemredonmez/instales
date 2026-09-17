"""`instilens` command line: the operational entrypoint for every pipeline stage."""

from datetime import date

import typer
import uvicorn

from instilens.config import settings
from instilens.db.session import get_engine, init_db, session_scope
from instilens.ingestion.kap import build_kap_adapter
from instilens.services import pipeline

app = typer.Typer(help="InstiLens — smart-money intelligence pipeline")
db_app = typer.Typer(help="Database utilities")
app.add_typer(db_app, name="db")
users_app = typer.Typer(help="User management")
app.add_typer(users_app, name="users")


@users_app.command("create")
def users_create(
    email: str,
    name: str = "",
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    plan: str = "FREE",
    role: str = "USER",
) -> None:
    from instilens.services import auth

    init_db(get_engine())
    with session_scope() as s:
        user = auth.register(s, email, password, name)
        user.plan, user.role = plan, role
        typer.echo(f"created {user.email} ({user.plan}, {user.role})")


@users_app.command("set-role")
def users_set_role(email: str, role: str) -> None:
    from sqlalchemy import select

    from instilens.domain.models import User

    with session_scope() as s:
        user = s.scalar(select(User).where(User.email == email.lower()))
        if user is None:
            raise typer.BadParameter(f"no user {email}")
        user.role = role
        typer.echo(f"{user.email} → {role}")


@db_app.command("init")
def db_init() -> None:
    init_db(get_engine())
    typer.echo(f"schema created at {_safe_url(settings.database_url)}")


def _adapter(market: str):
    if market == "US":
        from instilens.ingestion.sec import build_sec_adapter

        return build_sec_adapter()
    return build_kap_adapter()


@app.command()
def ingest(market: str = "TR") -> None:
    """Fetch new disclosures and store them raw (TR → KAP, US → SEC EDGAR)."""
    with session_scope() as s:
        typer.echo(f"ingested {pipeline.ingest(s, _adapter(market))} disclosures")


@app.command()
def prices(market: str = "TR", days: int = 400, symbols: str = typer.Option("", help="comma-separated subset")) -> None:
    """Pull daily OHLCV bars from the active price provider (Yahoo unless a vendor is configured) for every instrument in the market."""
    from instilens.ingestion.prices import load_prices

    with session_scope() as s:
        n = load_prices(s, market, days, [x for x in symbols.split(",") if x] or None)
        typer.echo(f"{market}: {n} bars written")


@app.command()
def fundamentals(market: str = "TR", symbols: str = typer.Option("", help="comma-separated subset; default: instruments seen in flows or on a watchlist")) -> None:
    """Pull reported statements (annual + quarterly) and the trailing metrics snapshot from the fundamentals provider."""
    from instilens.services.fundamentals import refresh

    with session_scope() as s:
        n = refresh(s, market, [x for x in symbols.split(",") if x] or None)
        typer.echo(f"{market}: {n} fundamentals rows written")


@app.command()
def insiders(symbols: str = typer.Option("", help="comma-separated US symbols; default: issuers seen in 13F flows or on a watchlist, stalest first"),
             days_back: int | None = typer.Option(None, help="days of filings to read per issuer (default sec_form4_days_back)")) -> None:
    """Pull SEC Form 4 insider transactions and the 8-K/10-K/10-Q filing index of US issuers from EDGAR."""
    from instilens.services.insiders import refresh

    with session_scope() as s:
        out = refresh(s, [x for x in symbols.split(",") if x] or None, days_back=days_back)
        typer.echo(f"US insiders: {out['issuers']} issuers · {out['filings']} filings · {out['form4']} Form 4 · {out['transactions']} transactions · {out['skipped']} skipped")


@app.command("sec-ciks")
def sec_ciks(all_: bool = typer.Option(False, "--all", help="re-map instruments that already carry a CIK")) -> None:
    """Map US instruments to their EDGAR CIK from the SEC's company_tickers.json (official, free)."""
    from instilens.ingestion.sec.tickers import refresh_ciks
    from instilens.services.insiders import build_client

    with session_scope() as s:
        typer.echo(f"mapped {refresh_ciks(s, build_client(), only_missing=not all_)} instruments")


@app.command("load-cusips")
def load_cusips(path: str) -> None:
    """Load a CUSIP→ticker CSV (cusip,symbol,name) so 13F rows resolve to tickers."""
    import csv

    from instilens.services.entities import EntityResolver

    with session_scope() as s, open(path, newline="", encoding="utf-8") as f:
        typer.echo(f"mapped {EntityResolver(s).load_cusip_map(list(csv.DictReader(f)))} instruments")


@app.command()
def parse() -> None:
    """Parse pending disclosures into normalized events and snapshots."""
    with session_scope() as s:
        typer.echo(f"parsed {pipeline.parse_pending(s)} disclosures")


@app.command()
def compute(as_of: str | None = typer.Option(None, help="YYYY-MM-DD, default today")) -> None:
    """Rebuild positions, then recompute signals and scores."""
    day = date.fromisoformat(as_of) if as_of else date.today()
    with session_scope() as s:
        typer.echo(f"position changes: {pipeline.rebuild_positions(s)}")
        typer.echo(f"instruments scored: {pipeline.compute_intelligence(s, day)}")
        from instilens.services.alerts import evaluate
        from instilens.services.notify import deliver_pending

        typer.echo(f"notifications: {evaluate(s, day)}")
        typer.echo(f"delivered: {deliver_pending(s)}")


@app.command()
def run(as_of: str | None = typer.Option(None), skip_prices: bool = False) -> None:
    """Full chain on REAL sources: migrate → ingest (KAP, SEC) → parse → prices (active provider) → positions → Form 4 insiders → intelligence → alerts → outcomes."""
    from instilens.ingestion.prices import load_prices
    from instilens.services.alerts import evaluate
    from instilens.services.notify import deliver_pending
    from instilens.services.outcomes import compute_outcomes

    init_db(get_engine())
    day = date.fromisoformat(as_of) if as_of else date.today()
    with session_scope() as s:
        from instilens.services.entities import EntityResolver

        EntityResolver(s).ensure_markets()
        cusips = settings.sec_fixture_dir.parent / "cusips_US.csv"  # real OpenFIGI map, refreshed by scripts/build_sec_fixtures.py
        if cusips.exists():
            import csv

            with open(cusips, newline="", encoding="utf-8") as f:
                EntityResolver(s).load_cusip_map(list(csv.DictReader(f)))
        for market in ("TR", "US"):
            typer.echo(f"[{market}] ingested {pipeline.ingest(s, _adapter(market))}")
        typer.echo(f"parsed {pipeline.parse_pending(s)}")
        if not skip_prices:
            for market in ("TR", "US"):
                typer.echo(f"[{market}] prices {load_prices(s, market, days=400)}")
        typer.echo(f"position changes {pipeline.rebuild_positions(s)}")
        if settings.sec_form4_enabled:  # after positions: the issuer universe is what the 13F diffs touched
            from instilens.services.insiders import refresh as refresh_insiders

            s.commit()  # the run so far is durable before the issuer walk (which commits issuer by issuer)
            typer.echo(f"[US] insiders {refresh_insiders(s)}")
        typer.echo(f"instruments scored {pipeline.compute_intelligence(s, day)}")
        typer.echo(f"notifications {evaluate(s, day)}")
        typer.echo(f"delivered {deliver_pending(s)}")
        typer.echo(f"signal outcomes {compute_outcomes(s)}")


@db_app.command("reset")
def db_reset(yes: bool = typer.Option(False, "--yes", help="drop everything without asking")) -> None:
    """Drop ALL data (including synthetic fixtures) and recreate the schema. Users are lost too."""
    if not yes and not typer.confirm("This deletes every table. Continue?"):
        raise typer.Abort()
    from instilens.domain.models import Base

    engine = get_engine()
    Base.metadata.drop_all(engine)
    with engine.begin() as conn:
        conn.exec_driver_sql("DROP TABLE IF EXISTS alembic_version")
    init_db(engine)
    typer.echo("database reset")


@app.command()
def api(host: str = "127.0.0.1", port: int = 8000, reload: bool = False) -> None:
    uvicorn.run("instilens.api.main:app", host=host, port=port, reload=reload)


@app.command()
def reindex(full: bool = typer.Option(False, "--full", help="drop and rebuild every row instead of only what changed since the last run"),
            market: str = typer.Option("", help="TR or US; default both")) -> None:
    """Bring the full-text search index (searchable_texts) up to date: disclosures, EDGAR filings, headlines, AI notes."""
    from instilens.services.search_index import MARKETS
    from instilens.services.search_index import reindex as reindex_texts

    if market and market.upper() not in MARKETS:
        raise typer.BadParameter(f"market must be one of {', '.join(MARKETS)}")
    with session_scope() as s:
        counts = reindex_texts(s, market.upper() or None, full=full)
        typer.echo("search index: " + " · ".join(f"{k} {v}" for k, v in counts.items()))


@app.command()
def ask(question: str, market: str = "TR") -> None:
    """Ask the research engine a question (Claude: ANTHROPIC_API_KEY or `ant auth login`; local: INSTILENS_AI_PROVIDER=local + a model)."""
    from instilens.ai import build_engine

    with session_scope() as s:
        answer = build_engine(s).ask(question, market)
    for call in answer.tool_calls:
        typer.echo(f"[tool] {call.name} {call.input}")
    typer.echo("")
    typer.echo(answer.answer)
    typer.echo(f"\n({answer.model}, {answer.usage})")


@app.command()
def scheduler() -> None:
    """Run the background worker (ingest/parse/prices/compute on a cron cadence)."""
    from instilens.scheduler import main

    main()


@app.command()
def feed() -> None:
    """Run the quote feed: header quotes refreshed while a market is open and pushed to open tabs (see services/feed)."""
    from instilens.services.feed import main

    main()


@app.command("kap-test")
def kap_test(out: str = "/tmp/kap-api-probe.json") -> None:
    """Probe the official KAP API with the configured key/secret and dump raw responses for inspection."""
    import json

    from instilens.ingestion.kap.api_adapter import probe

    if not settings.kap_api_auth_header and not (settings.kap_api_key and settings.kap_api_secret):
        raise typer.BadParameter("set INSTILENS_KAP_API_AUTH_HEADER (or KEY+SECRET) in backend/.env")
    results: dict = {}

    def dump(name: str, value) -> None:
        results[name] = value
        typer.echo(f"✓ {name}: {str(value)[:160]}")

    try:
        probe(settings.kap_api_base_url, settings.kap_api_key or "", settings.kap_api_secret or "", dump, auth_header=settings.kap_api_auth_header, auth_mode=settings.kap_api_auth_mode)
    finally:
        with open(out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=1, default=str)
        typer.echo(f"raw responses saved to {out}")


@app.command()
def news(market: str = "TR") -> None:
    """Pull headlines from the configured RSS feeds (and NewsAPI if a key is set)."""
    from instilens.ingestion.news import fetch_feeds
    from instilens.services.news_rules import seed_default_rules

    with session_scope() as s:
        seeded = seed_default_rules(s)
        if seeded:
            typer.echo(f"seeded {seeded} default news rules")
        typer.echo(f"{market}: +{fetch_feeds(s, market, newsapi_key=settings.newsapi_key)} headlines")
        if settings.ai_news_enabled and settings.anthropic_api_key:
            from instilens.ai.news_enrich import enrich

            try:
                typer.echo(f"{market}: ai-tagged {enrich(s, market)}")
            except Exception as exc:  # enrichment is optional; a bad key must not block headlines
                typer.echo(f"{market}: ai tagging skipped ({type(exc).__name__}: {str(exc)[:80]})")


@app.command("vapid-keys")
def vapid_keys() -> None:
    """Generate a VAPID key pair for Web Push; paste the two lines into backend/.env."""
    import base64

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    key = ec.generate_private_key(ec.SECP256R1())
    priv = key.private_numbers().private_value.to_bytes(32, "big")
    pub = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    b64 = lambda b: base64.urlsafe_b64encode(b).rstrip(b"=").decode()  # noqa: E731
    typer.echo(f"INSTILENS_VAPID_PUBLIC_KEY={b64(pub)}")
    typer.echo(f"INSTILENS_VAPID_PRIVATE_KEY={b64(priv)}")


@app.command("releases-prune")
def releases_prune() -> None:
    """Drop artifacts whose file name carries a different version than their release (leftovers from builds that
    joined the same draft), keeping one file per platform/kind/extension."""
    from sqlalchemy import select

    from instilens.domain.models import Release
    from instilens.services import releases as rs

    with session_scope() as s:
        removed = 0
        for rel in s.scalars(select(Release)):
            seen: set[tuple[str, str, str]] = set()
            for f in sorted(rel.files, key=lambda x: x.uploaded_at, reverse=True):
                key = (f.platform, f.kind, rs._ext(f.filename))
                if rel.version not in f.filename or key in seen:
                    rs.file_path(f).unlink(missing_ok=True)
                    s.delete(f)
                    removed += 1
                else:
                    seen.add(key)
        typer.echo(f"removed {removed} stale artifact(s)")


def _safe_url(url: str) -> str:
    """Database URL without the password — this line ends up in deploy logs."""
    import re as _re

    return _re.sub(r"://([^:/@]+):[^@]*@", r"://\1:***@", url)
