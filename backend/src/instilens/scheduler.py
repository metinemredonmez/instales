"""Long-running worker: runs the pipeline stages on a schedule. One process, no broker.

Cadence (Europe/Istanbul):
  KAP ingest+parse   every 5 min 17:30–23:00 on weekdays (disclosure rush), every 30 min otherwise
  SEC ingest         daily 08:00
  prices             daily 19:30 (TR close) and 00:30 (US close)
  compute            after every ingest that stored something, and nightly 02:00 regardless
"""

from __future__ import annotations

import logging
from datetime import date

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from instilens.ai.tts import warm
from instilens.db.session import get_engine, init_db, session_scope
from instilens.ingestion.kap import build_kap_adapter
from instilens.ingestion.prices import load_prices
from instilens.ingestion.sec import build_sec_adapter
from instilens.services import live, pipeline
from instilens.services.alerts import evaluate
from instilens.services.notify import deliver_brief, deliver_pending
from instilens.services.outcomes import compute_outcomes

log = logging.getLogger("instilens.scheduler")
TZ = "Europe/Istanbul"


def compute() -> None:
    with session_scope() as s:
        log.info("positions %s", pipeline.rebuild_positions(s))
        scored = pipeline.compute_intelligence(s, date.today())
        log.info("scored %s", scored)
        log.info("notifications %s", evaluate(s, date.today()))
        log.info("delivered %s", deliver_pending(s))
        log.info("outcomes %s", compute_outcomes(s))
        live.publish(s, "compute", payload={"scored": scored})  # open tabs refetch radar/stock/screener/watchlist
        live.prune(s)


def ingest_kap() -> None:
    with session_scope() as s:
        n = pipeline.ingest(s, build_kap_adapter())
        parsed = pipeline.parse_pending(s)
    log.info("KAP ingested %s parsed %s", n, parsed)
    if parsed:
        compute()


def ingest_sec() -> None:
    with session_scope() as s:
        n = pipeline.ingest(s, build_sec_adapter())
        parsed = pipeline.parse_pending(s)
    log.info("SEC ingested %s parsed %s", n, parsed)
    if parsed:
        compute()


def news_pull() -> None:
    from instilens.config import settings
    from instilens.ingestion.news import fetch_feeds

    if not settings.news_enabled:
        return
    from instilens.ai.news_enrich import enrich

    with session_scope() as s:
        for market in ("TR", "US"):
            added = fetch_feeds(s, market, newsapi_key=settings.newsapi_key)
            log.info("%s news +%s", market, added)
            if added:
                live.publish(s, "news", market=market, payload={"added": added})
            if settings.ai_news_enabled and settings.anthropic_api_key:
                try:
                    log.info("%s news ai-tagged %s", market, enrich(s, market))
                except Exception as exc:  # AI is an enrichment; never break the feed
                    log.warning("news enrich failed: %s", exc)


def briefs() -> None:
    from instilens.ai.assess import daily_brief
    from instilens.config import settings

    if not settings.anthropic_api_key:
        return
    with session_scope() as s:
        for market in ("TR", "US"):
            try:
                note = daily_brief(s, market, force=True)  # Turkish
                note_en = daily_brief(s, market, force=True, lang="en")  # English, so EN users don't wait on first open
                # users receive the brief of the markets in their brief_markets; brief_deliveries makes a re-run send nothing twice
                log.info("%s brief %s, delivered %s", market, "ok" if note else "skipped", deliver_brief(s, note) if note else 0)
                if note:
                    live.publish(s, "brief", market=market)
                s.flush()
                log.info("%s tts warmed: %s files", market, warm(note, s) + warm(note_en, s))
            except Exception as exc:
                log.warning("brief %s failed: %s", market, exc)


def prices(market: str) -> None:
    with session_scope() as s:
        log.info("%s prices %s", market, load_prices(s, market, days=30))


def _fresh(job):
    """Run a job with the latest admin overrides applied (settings can change between runs)."""
    from functools import wraps

    from instilens.services import runtime_settings

    @wraps(job)
    def run(*a, **kw):
        try:
            with session_scope() as s:
                runtime_settings.apply(s)
        except Exception as exc:  # noqa: BLE001
            log.warning("runtime settings not applied: %s", exc)
        return job(*a, **kw)

    return run


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    init_db(get_engine())
    sched = BlockingScheduler(timezone=TZ, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})
    sched.add_job(_fresh(ingest_kap), CronTrigger(day_of_week="mon-fri", hour="17-22", minute="*/5", timezone=TZ), id="kap_rush")
    sched.add_job(_fresh(ingest_kap), CronTrigger(hour="0-16,23", minute="*/30", timezone=TZ), id="kap_offpeak")
    sched.add_job(_fresh(ingest_sec), CronTrigger(hour=8, minute=0, timezone=TZ), id="sec_daily")
    sched.add_job(_fresh(prices), CronTrigger(hour=19, minute=30, timezone=TZ), args=["TR"], id="prices_tr")
    sched.add_job(_fresh(prices), CronTrigger(hour=0, minute=30, timezone=TZ), args=["US"], id="prices_us")
    sched.add_job(_fresh(compute), CronTrigger(hour=2, minute=0, timezone=TZ), id="nightly_compute")
    sched.add_job(_fresh(news_pull), CronTrigger(minute="*/10", timezone=TZ), id="news")
    sched.add_job(_fresh(briefs), CronTrigger(hour=8, minute=30, timezone=TZ), id="briefs")
    log.info("scheduler up: %s", [j.id for j in sched.get_jobs()])
    sched.start()


if __name__ == "__main__":
    main()
