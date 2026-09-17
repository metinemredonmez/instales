"""Candles for the chart widget: one OHLCV series per (market, symbol, interval).

Daily bars come from `market_prices` when every row of the range carries open/high/low (the nightly `load_prices`
writes them); a range with a close-only legacy row (the CSV loader, an old hole) is answered by the active
provider's `daily_bars` for the whole range instead — the chart never draws a padded candle. Intraday bars
always come from the provider (`intraday_bars`). Every provider answer sits in a 60 s in-process cache per
(market, symbol, interval), so a page-load storm does not become a Yahoo storm, and the last good answer is
remembered: an outage serves it, logged, under its own `as_of` — never a made-up bar and never an old one
presented as fresh. With nothing remembered the outage propagates (ProviderUnavailable → 503 at the route).

Timestamps are epoch seconds UTC. A daily bar is stamped at 00:00 UTC of its session date — what the chart
library reads as a calendar day — and an intraday bar at its open instant. `tz` names the exchange's zone so
the widget can label the axis in local session time. `delay` says how far behind the bars are — the provider's
own word for its answers, `eod` for table rows, which are last night's close whoever printed them — and `stale`
appears (true) only on an answer served through an outage, like a /quotes entry.
"""

from __future__ import annotations

import logging
import threading
from collections import OrderedDict
from datetime import UTC, date, datetime, time, timedelta
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.domain.enums import Market
from instilens.domain.models import Instrument, MarketPrice, MarketRow
from instilens.ingestion.prices.provider import (
    INTRADAY_INTERVALS,
    PROVIDERS,
    PriceProvider,
    ProviderUnavailable,
    resolve_provider,
)
from instilens.services import entities
from instilens.services.quotes import MARKETS

log = logging.getLogger(__name__)

Interval = Literal["5m", "15m", "1h", "1d"]
INTERVALS: tuple[str, ...] = (*INTRADAY_INTERVALS, "1d")
CACHE_SECONDS = 60
CACHE_KEYS = 256  # keys remembered per process; past it the least recently served answer is dropped (≤ 1000 bar dicts each)
LOOKBACK_MIN, LOOKBACK_DEFAULT, LOOKBACK_MAX = 10, 300, 1000

Key = tuple[str, str, str]  # (market, symbol, interval)
_lock = threading.Lock()  # guards the two dicts below; never held across a fetch
# key → {"fetched_at", "lookback", "source", "delay", "bars"}: the newest good provider answer, least recently served first.
_cache: OrderedDict[Key, dict[str, Any]] = OrderedDict()
# One lock per key: concurrent callers of the same key share one fetch, other keys and cache hits never wait for it.
_fetching: dict[Key, threading.Lock] = {}


def reset() -> None:
    with _lock:
        _cache.clear()
        _fetching.clear()


def _day_epoch(d: date) -> int:
    return int(datetime.combine(d, time(0), tzinfo=UTC).timestamp())


def _bar_json(t: int, o, h, lo, c, v) -> dict[str, Any]:
    return {"t": t, "o": float(o), "h": float(h), "l": float(lo), "c": float(c), "v": int(v) if v is not None else None}


def _from_table(session: Session, instrument: Instrument, lookback: int) -> tuple[list[dict[str, Any]], str] | None:
    """The latest `lookback` daily rows as bars, oldest first, with the newest row's `source`; None when the range
    is empty, any row lacks open/high/low, or any row was written by something other than a provider (a CSV close
    laid over a provider row keeps that row's open/high/low) — the provider then answers the whole range."""
    rows = session.scalars(
        select(MarketPrice).where(MarketPrice.instrument_id == instrument.id).order_by(MarketPrice.trade_date.desc()).limit(lookback)
    ).all()
    if not rows or any(r.open is None or r.high is None or r.low is None or r.source not in PROVIDERS for r in rows):
        return None
    rows.reverse()
    return [_bar_json(_day_epoch(r.trade_date), r.open, r.high, r.low, r.close, r.volume) for r in rows], rows[-1].source


def _daily_days(lookback: int) -> int:
    """Calendar days that hold `lookback` sessions: 7/5 for weekends plus two weeks for holidays."""
    return lookback * 7 // 5 + 14


def _fetch(provider: PriceProvider, market: str, symbol: str, interval: str, lookback: int) -> list[dict[str, Any]]:
    """The latest `lookback` bars of `interval` from the provider. A daily bar Yahoo left a hole in (open/high/low
    None) is dropped like an intraday one — the contract is four prices per candle."""
    if interval == "1d":
        bars = provider.daily_bars(market, [symbol], date.today() - timedelta(days=_daily_days(lookback)))
        out = [
            _bar_json(_day_epoch(b.trade_date), b.open, b.high, b.low, b.close, b.volume)
            for b in sorted(bars, key=lambda b: b.trade_date)
            if b.open is not None and b.high is not None and b.low is not None
        ]
    else:
        out = [_bar_json(int(c.at.timestamp()), c.open, c.high, c.low, c.close, c.volume) for c in provider.intraday_bars(market, symbol, interval, lookback)]
    return out[-lookback:]


def _remembered(key: Key, provider: PriceProvider, lookback: int, now: datetime) -> tuple[dict[str, Any] | None, bool]:
    """Under `_lock`: the cached entry for `key` (None when nothing is remembered) and whether it still answers —
    younger than CACHE_SECONDS, from this provider, holding at least `lookback` bars. A hit moves the key to the
    recent end of the cache."""
    entry = _cache.get(key)
    fresh = entry is not None and entry["source"] == provider.name and entry["lookback"] >= lookback and (now - entry["fetched_at"]).total_seconds() < CACHE_SECONDS
    if fresh:
        _cache.move_to_end(key)
    return entry, fresh


def _from_provider(provider: PriceProvider, market: str, symbol: str, interval: str, lookback: int, now: datetime) -> dict[str, Any]:
    """The cached answer for the key while it is younger than CACHE_SECONDS, came from this provider and holds at
    least `lookback` bars; otherwise a fresh fetch, remembered. The fetch runs under the key's own lock, so
    concurrent callers of one key share one fetch while every other key (and every cache hit) goes on — a slow
    Yahoo call for AAPL never delays ASELS. An outage with a remembered answer serves that answer under its own
    `fetched_at`, marked `stale`, and logs; with none it propagates."""
    key: Key = (market, symbol, interval)
    with _lock:
        entry, fresh = _remembered(key, provider, lookback, now)
        if fresh:
            return {**entry, "bars": entry["bars"][-lookback:]}
        fetching = _fetching.setdefault(key, threading.Lock())
    with fetching:
        with _lock:  # a caller that queued behind a fetch of this key finds its answer here
            entry, fresh = _remembered(key, provider, lookback, now)
            if fresh:
                return {**entry, "bars": entry["bars"][-lookback:]}
        try:
            bars = _fetch(provider, market, symbol, interval, lookback)
        except ProviderUnavailable as exc:
            if entry is None:
                raise
            log.warning("candles: %s %s %s %s failed, serving the answer of %s: %s", provider.name, market, symbol, interval, entry["fetched_at"].isoformat(), exc)
            return {**entry, "stale": True, "bars": entry["bars"][-lookback:]}
        entry = {"fetched_at": now, "lookback": lookback, "source": provider.name, "delay": provider.status().delay, "bars": bars}
        with _lock:
            _cache[key] = entry
            _cache.move_to_end(key)
            while len(_cache) > CACHE_KEYS:
                old, _ = _cache.popitem(last=False)
                if old in _fetching and not _fetching[old].locked():
                    del _fetching[old]
        return {**entry, "bars": bars[-lookback:]}


def candles(session: Session, market: str, symbol: str, interval: str, lookback: int = LOOKBACK_DEFAULT, now: datetime | None = None) -> dict[str, Any] | None:
    """The /stocks/{symbol}/candles payload; None for a symbol unknown in `market`. `lookback` is the most bars
    returned (the route bounds it to LOOKBACK_MIN..LOOKBACK_MAX). Raises ProviderUnavailable when the provider
    has to answer and cannot, with no remembered answer to fall back on."""
    if interval not in INTERVALS:
        raise ValueError(f"unknown interval {interval!r} (one of {', '.join(INTERVALS)})")
    now = now or datetime.now(UTC)
    instrument = session.scalar(select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol.upper()))
    if instrument is None:
        return None
    market_row = session.get(MarketRow, market)
    currency = market_row.currency if market_row is not None else entities.MARKETS[Market(market)].currency
    table = _from_table(session, instrument, lookback) if interval == "1d" else None
    if table is not None:
        bars, source = table
        # End-of-day rows are never a live print, whichever provider wrote them.
        answer = {"fetched_at": now, "source": source, "delay": "eod", "bars": bars}
    else:
        answer = _from_provider(resolve_provider(), market, instrument.symbol, interval, lookback, now)
    return {
        "symbol": instrument.symbol,
        "name": instrument.name,
        "market": market,
        "currency": currency,
        "interval": interval,
        "source": answer["source"],
        "delay": answer["delay"],
        "delayed": answer["delay"] != "realtime",
        "as_of": answer["fetched_at"].isoformat(),
        "tz": MARKETS[market].tz,
        "bars": answer["bars"],
        **({"stale": True} if answer.get("stale") else {}),
    }
