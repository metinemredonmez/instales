"""Header quotes (USD/TRY, EUR/TRY, BIST 100, S&P 500) and market-hours state.

Prices come from the active price provider (`ingestion/prices/provider.resolve_provider` — Yahoo unless a
licensed vendor is configured); every quote says which one (`source`) and whether it is `delayed`. One
in-process cache (60 s) shields the provider from a page-load storm; the last good value per ticker is
remembered so an outage shows *stale* numbers flagged as such, never a made-up one and never an old number
presented as fresh. A ticker the provider cannot answer and we have never seen is simply omitted. The
`instilens feed` process bypasses the cache (`force=True`) and pushes the payload to open tabs.

Market hours are pure functions of a clock passed in explicitly (`now`), so tests can pin any instant.
Exchange holidays are not modelled; INSTILENS_MARKET_HOLIDAYS_TR (admin-editable) is the escape hatch.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from instilens.config import settings
from instilens.ingestion.prices.provider import PriceProvider, resolve_provider

log = logging.getLogger(__name__)

CACHE_SECONDS = 60


@dataclass(frozen=True)
class QuoteSpec:
    key: str
    ticker: str  # provider symbol (Yahoo convention; a vendor adapter maps it)
    label: str
    currency: str
    decimals: int


QUOTES: tuple[QuoteSpec, ...] = (
    QuoteSpec("USDTRY", "USDTRY=X", "USD/TRY", "TRY", 4),
    QuoteSpec("EURTRY", "EURTRY=X", "EUR/TRY", "TRY", 4),
    QuoteSpec("XU100", "XU100.IS", "BIST 100", "TRY", 0),
    QuoteSpec("SPX", "^GSPC", "S&P 500", "USD", 0),
)


# ---------------------------------------------------------------------------------------------
# Market hours


@dataclass(frozen=True)
class Segment:
    state: str  # "pre" | "open" | "post"
    start: time
    end: time


@dataclass(frozen=True)
class MarketHours:
    tz: str
    segments: tuple[Segment, ...]  # in wall-clock order, all within one calendar day


# BIST continuous session; NYSE core session with the electronic pre/post windows.
MARKETS: dict[str, MarketHours] = {
    "TR": MarketHours("Europe/Istanbul", (Segment("open", time(10, 0), time(18, 0)),)),
    "US": MarketHours("America/New_York", (Segment("pre", time(4, 0), time(9, 30)), Segment("open", time(9, 30), time(16, 0)), Segment("post", time(16, 0), time(20, 0)))),
}


def _is_trading_day(d: date, holidays: frozenset[date]) -> bool:
    return d.weekday() < 5 and d not in holidays


def market_state(market: str, now: datetime, holidays: Iterable[date] = ()) -> dict[str, Any]:
    """State of one market at `now` (must be tz-aware) and the instant of the next transition."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    hours = MARKETS[market]
    tz = ZoneInfo(hours.tz)
    local = now.astimezone(tz)
    today = local.date()
    hol = frozenset(holidays)

    def at(d: date, t: time) -> datetime:
        return datetime.combine(d, t, tzinfo=tz)

    state, next_change = "closed", None
    if _is_trading_day(today, hol):
        for seg in hours.segments:
            if at(today, seg.start) <= local < at(today, seg.end):
                state, next_change = seg.state, at(today, seg.end)
                break
            if local < at(today, seg.start):
                next_change = at(today, seg.start)
                break
    if next_change is None:  # closed for the rest of today → first segment of the next trading day
        d = today + timedelta(days=1)
        while not _is_trading_day(d, hol):
            d += timedelta(days=1)
        next_change = at(d, hours.segments[0].start)
    return {"state": state, "label_key": f"market.{state}", "next_change_at": next_change.isoformat(), "tz": hours.tz}


def tr_holidays() -> list[date]:
    """INSTILENS_MARKET_HOLIDAYS_TR parsed; entries that are not ISO dates are logged and ignored."""
    out: list[date] = []
    for raw in settings.market_holidays_tr:
        try:
            out.append(date.fromisoformat(str(raw).strip()))
        except ValueError:
            log.warning("market_holidays_tr: ignoring non-ISO entry %r", raw)
    return out


def markets(now: datetime) -> dict[str, dict[str, Any]]:
    return {"TR": market_state("TR", now, tr_holidays()), "US": market_state("US", now)}


# ---------------------------------------------------------------------------------------------
# Quotes

_lock = threading.Lock()
_cache: dict[str, Any] = {"fetched_at": None, "provider": None, "quotes": {}}  # fetched_at: datetime | None
_last_good: dict[str, dict[str, Any]] = {}


def reset() -> None:
    with _lock:
        _cache["fetched_at"], _cache["provider"], _cache["quotes"] = None, None, {}
        _last_good.clear()


def _refresh(now: datetime, provider: PriceProvider) -> None:
    tickers = [q.ticker for q in QUOTES]
    try:
        ticks = provider.quotes(tickers)
    except Exception as exc:  # noqa: BLE001 — provider outage: keep last-good, mark stale below
        log.warning("quotes: %s fetch failed: %s", provider.name, exc)
        ticks = {}
    delayed = provider.status().delay != "realtime"
    fresh: dict[str, dict[str, Any]] = {}
    for spec in QUOTES:
        tick = ticks.get(spec.ticker)
        if tick is None:
            continue
        # updated_at is when the provider last confirmed the price (its own timestamp when it has one, else the
        # fetch instant); bar_date is the session it was printed in, so a weekend read of Friday's close is not
        # mistaken for a Saturday print.
        fresh[spec.key] = {"key": spec.key, "label": spec.label, "price": round(tick.price, spec.decimals), "change_pct": tick.change_pct,
                           "currency": spec.currency, "updated_at": (tick.at or now).isoformat(), "decimals": spec.decimals,
                           "bar_date": tick.bar_date.isoformat() if tick.bar_date else None, "source": provider.name, "delayed": delayed}
    _last_good.update(fresh)
    _cache["fetched_at"], _cache["provider"], _cache["quotes"] = now, provider.name, fresh


def snapshot(now: datetime | None = None, *, force: bool = False) -> dict[str, Any]:
    """The /quotes payload. Refreshes at most once per CACHE_SECONDS (concurrent callers share one fetch), or
    immediately when the active provider changed since the cached fetch; `force` skips the cache altogether
    (the feed process, which is the only caller that should)."""
    now = now or datetime.now(UTC)
    provider = resolve_provider()
    with _lock:
        fetched_at = _cache["fetched_at"]
        expired = fetched_at is None or (now - fetched_at).total_seconds() >= CACHE_SECONDS
        if force or expired or _cache["provider"] != provider.name:
            _refresh(now, provider)
        fresh = _cache["quotes"]
        quotes = []
        for spec in QUOTES:
            if spec.key in fresh:
                quotes.append(dict(fresh[spec.key]))
            elif spec.key in _last_good:
                quotes.append({**_last_good[spec.key], "stale": True})
    return {"as_of": now.isoformat(), "quotes": quotes, "markets": markets(now)}
