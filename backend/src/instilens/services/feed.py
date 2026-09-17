"""Quote feed: the one process (`instilens feed`) that refreshes the header strip and pushes it to open tabs.

Every `quotes_interval_s` while BIST or NYSE is in a pre/open/post window — otherwise every 10 minutes, or
sooner if a session opens before that — it builds the /quotes snapshot straight from the provider (bypassing
the API's 60 s cache) and appends a `quotes` live event only when a price, change, stale flag or market
state differs from the last one it published, so idle markets add no rows. A heartbeat row (`_feed_status`
in app_settings) tells /admin/providers whether the feed is alive and carries the provider's own status as this
process sees it (the API workers have instances of their own that may never have fetched); a provider outage
lands in that row and in the log, never in a dead process. Old live events are swept once a day here too, so the
table stays bounded while the scheduler's compute job is stopped or failing.
"""

from __future__ import annotations

import logging
import signal
import threading
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.db.session import session_scope
from instilens.ingestion.prices.provider import resolve_provider
from instilens.services import live, quotes, runtime_settings

log = logging.getLogger("instilens.feed")

HEARTBEAT_KEY = "_feed_status"
IDLE_INTERVAL_S = 600
ACTIVE_STATES = ("pre", "open", "post")
PRUNE_EVERY = timedelta(hours=24)


@dataclass
class FeedState:
    last: tuple | None = None  # fingerprint of the last snapshot seen (published or, when empty, deliberately not)
    published: int = 0
    pruned_at: datetime | None = None  # last sweep of old live events


def interval_s(now: datetime) -> int:
    """`quotes_interval_s` while any market trades; idle otherwise, but never sleeping past the next session start."""
    states = quotes.markets(now)
    if any(m["state"] in ACTIVE_STATES for m in states.values()):
        return settings.quotes_interval_s
    until_next = min((datetime.fromisoformat(m["next_change_at"]) - now).total_seconds() for m in states.values())
    return max(1, min(IDLE_INTERVAL_S, int(until_next) + 1))


def fingerprint(snapshot: dict[str, Any]) -> tuple:
    """What a tab would see change: per-quote price / change / stale flag, plus each market's state. `as_of` and
    `updated_at` move every tick and are deliberately left out."""
    quotes_part = tuple((q["key"], q["price"], q["change_pct"], bool(q.get("stale"))) for q in snapshot["quotes"])
    markets_part = tuple((k, m["state"]) for k, m in sorted(snapshot["markets"].items()))
    return quotes_part, markets_part


def _write_heartbeat(hb: dict[str, Any]) -> None:
    try:
        with session_scope() as s:
            runtime_settings.store_json(s, HEARTBEAT_KEY, hb, actor="feed")
    except Exception as exc:  # noqa: BLE001 — a heartbeat miss is reported by /admin/providers, not fatal here
        log.warning("feed: heartbeat not written: %s", exc)


def _prune_if_due(state: FeedState, now: datetime) -> None:
    """Sweep live events older than a day, once a day. The scheduler's compute job does the same, but the feed keeps
    appending rows even while that job is stopped or failing, so it does not rely on it."""
    if state.pruned_at is not None and now - state.pruned_at < PRUNE_EVERY:
        return
    try:
        with session_scope() as s:
            swept = live.prune(s)
        state.pruned_at = now
        log.info("feed: swept %s old live events", swept)
    except Exception as exc:  # noqa: BLE001 — like a heartbeat miss: logged, retried next tick, never fatal
        log.warning("feed: live events not swept: %s", exc)


def run_once(state: FeedState, now: datetime | None = None) -> dict[str, Any]:
    """One iteration: latest admin overrides → forced snapshot → publish if changed → heartbeat → daily sweep.
    Never raises; whatever failed is in the heartbeat's `error` (and the log). Returns the heartbeat written."""
    now = now or datetime.now(UTC)
    provider_name: str | None = None
    provider_status: dict[str, Any] | None = None
    error: str | None = None
    try:
        with session_scope() as s:
            runtime_settings.apply(s)
        snap = quotes.snapshot(now, force=True)
        provider = resolve_provider()
        status = provider.status()
        provider_name, provider_status, error = provider.name, status.as_dict(), status.error
        fp = fingerprint(snap)
        if fp != state.last:
            # An empty list (provider down at start, nothing remembered yet in this process) would blank the strip in
            # every open tab, which still holds the API's last-good numbers; the fingerprint moves anyway so the first
            # real payload is published.
            if snap["quotes"]:
                with session_scope() as s:
                    live.publish(s, "quotes", payload=snap)
                state.published += 1
                log.info("feed: published %s quotes via %s", len(snap["quotes"]), provider.name)
            else:
                log.info("feed: no quotes to publish via %s", provider.name)
            state.last = fp
    except Exception as exc:  # noqa: BLE001 — one bad tick must not stop the loop
        log.warning("feed: iteration failed: %s", exc)
        error = f"{type(exc).__name__}: {exc}"[:200]
    hb = {"running": True, "last_run_at": now.isoformat(), "interval_s": interval_s(now), "published": state.published, "provider": provider_name, "provider_status": provider_status, "error": error}
    _write_heartbeat(hb)
    _prune_if_due(state, now)
    return hb


def run_forever(stop_event: threading.Event) -> None:
    state = FeedState()
    log.info("feed up: provider=%s interval=%ss idle=%ss", settings.price_provider, settings.quotes_interval_s, IDLE_INTERVAL_S)
    while not stop_event.is_set():
        hb = run_once(state)
        stop_event.wait(hb["interval_s"])
    _write_heartbeat({"running": False, "last_run_at": datetime.now(UTC).isoformat(), "interval_s": settings.quotes_interval_s, "published": state.published, "provider": settings.price_provider, "provider_status": None, "error": None})
    log.info("feed stopped after %s publishes", state.published)


def status(session: Session, now: datetime | None = None) -> dict[str, Any]:
    """The heartbeat as /admin/providers reports it. `running` is true only when the feed said so AND its last
    run is younger than 3 × its interval — a killed process never gets to say goodbye. `provider_status` is the
    provider's `status().as_dict()` as the feed process saw it on that run (None from a heartbeat that predates it)."""
    now = now or datetime.now(UTC)
    hb = runtime_settings.load_json(session, HEARTBEAT_KEY) or {}
    last = hb.get("last_run_at")
    interval = int(hb.get("interval_s") or settings.quotes_interval_s)
    age = (now - datetime.fromisoformat(last)).total_seconds() if last else None
    running = bool(hb.get("running")) and age is not None and age < 3 * interval
    return {"running": running, "last_run_at": last, "interval_s": interval, "published": int(hb.get("published") or 0), "provider": hb.get("provider"),
            "provider_status": hb.get("provider_status"), "error": hb.get("error")}


def main() -> None:
    """Entry point of `instilens feed`: runs until SIGTERM/SIGINT (pm2 stop/reload)."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    run_forever(stop)
