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

from instilens.db.session import get_engine, init_db, session_scope
from instilens.ingestion.kap import build_kap_adapter
from instilens.ingestion.prices.yahoo import load_prices
from instilens.ingestion.sec import build_sec_adapter
from instilens.services import pipeline
from instilens.services.alerts import evaluate
from instilens.services.outcomes import compute_outcomes

log = logging.getLogger("instilens.scheduler")
TZ = "Europe/Istanbul"


def compute() -> None:
    with session_scope() as s:
        log.info("positions %s", pipeline.rebuild_positions(s))
        log.info("scored %s", pipeline.compute_intelligence(s, date.today()))
        log.info("notifications %s", evaluate(s, date.today()))
        log.info("outcomes %s", compute_outcomes(s))


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


def prices(market: str) -> None:
    with session_scope() as s:
        log.info("%s prices %s", market, load_prices(s, market, days=30))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    init_db(get_engine())
    sched = BlockingScheduler(timezone=TZ, job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 600})
    sched.add_job(ingest_kap, CronTrigger(day_of_week="mon-fri", hour="17-22", minute="*/5", timezone=TZ), id="kap_rush")
    sched.add_job(ingest_kap, CronTrigger(hour="0-16,23", minute="*/30", timezone=TZ), id="kap_offpeak")
    sched.add_job(ingest_sec, CronTrigger(hour=8, minute=0, timezone=TZ), id="sec_daily")
    sched.add_job(prices, CronTrigger(hour=19, minute=30, timezone=TZ), args=["TR"], id="prices_tr")
    sched.add_job(prices, CronTrigger(hour=0, minute=30, timezone=TZ), args=["US"], id="prices_us")
    sched.add_job(compute, CronTrigger(hour=2, minute=0, timezone=TZ), id="nightly_compute")
    log.info("scheduler up: %s", [j.id for j in sched.get_jobs()])
    sched.start()


if __name__ == "__main__":
    main()
