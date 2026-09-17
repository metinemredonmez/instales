"""Price provider interface: one contract behind the header quotes, the daily `market_prices` loader and the
chart widget's intraday candles.

A provider owns its symbol convention (Yahoo wants `ASELS.IS`, a vendor may want `ASELS.E`) and reports its
own delay/entitlement honestly through `status()`. Two providers exist: Yahoo (delayed, unofficial — dev and
beta) and a Matriks slot that stays "not configured" until the vendor documentation arrives. `resolve_provider`
is the single switch every caller goes through; an unconfigured choice falls back to Yahoo and says so once.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Protocol

from instilens.config import settings

log = logging.getLogger("instilens.prices")

PROVIDERS: tuple[str, ...] = ("yahoo", "matriks")
DEFAULT_PROVIDER = "yahoo"
# Bar sizes a provider is asked for below one day (services/candles adds "1d", which the table or `daily_bars` answers).
INTRADAY_INTERVALS: tuple[str, ...] = ("5m", "15m", "1h")


class ProviderUnavailable(RuntimeError):
    """The provider cannot answer right now (outage, missing adapter, refused request)."""


class ProviderNotConfigured(ProviderUnavailable):
    """The chosen provider has no credentials/endpoints yet (Matriks before the vendor doc)."""


@dataclass(frozen=True)
class Bar:
    """One daily OHLCV bar. `symbol` is the instrument symbol the caller asked for, never the vendor's."""

    symbol: str
    trade_date: date
    open: Decimal | None
    high: Decimal | None
    low: Decimal | None
    close: Decimal
    volume: int | None


@dataclass(frozen=True)
class Candle:
    """One intraday OHLCV bar. `at` is the bar's open instant, tz-aware UTC (the API serialises it as epoch
    seconds); the four prices are all present — a bar Yahoo left a hole in is dropped, never padded."""

    symbol: str
    at: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: int | None


@dataclass(frozen=True)
class Tick:
    """Last print for one header ticker. `key` is the ticker as requested (the provider's own symbol);
    `at` is the exchange timestamp when the provider gives one, None when only the fetch time is known."""

    key: str
    price: float
    change_pct: float | None  # versus the previous session's close; None with a single bar
    bar_date: date | None  # session the price belongs to
    at: datetime | None


@dataclass(frozen=True)
class ProviderStatus:
    name: str
    configured: bool
    connected: bool
    delay: str  # "realtime" | "delayed" | "eod"
    last_tick_at: datetime | None
    error: str | None
    note: str | None

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["last_tick_at"] = self.last_tick_at.isoformat() if self.last_tick_at else None
        return out


class PriceProvider(Protocol):
    name: str

    def quotes(self, tickers: list[str]) -> dict[str, Tick]:
        """Last price per ticker (the provider's own symbols). Tickers it cannot answer are simply absent."""
        ...

    def daily_bars(self, market: str, symbols: list[str], start: date) -> list[Bar]:
        """Daily OHLCV from `start` for instrument symbols of `market`; symbols it cannot map are skipped."""
        ...

    def intraday_bars(self, market: str, symbol: str, interval: str, lookback: int) -> list[Candle]:
        """The latest `lookback` bars of `interval` (one of INTRADAY_INTERVALS) for one instrument symbol of
        `market`, oldest first, regular session only. A symbol it cannot map yields []; an answer with no bars
        at all is an outage (ProviderUnavailable), the same verdict `daily_bars` gives an empty frame."""
        ...

    def status(self) -> ProviderStatus: ...


class StreamingPriceProvider(PriceProvider, Protocol):
    """Optional: providers with a push channel (WebSocket/MQTT). Yahoo has none."""

    def stream(self, symbols: list[str]) -> AsyncIterator[Tick]: ...


def build_provider(name: str) -> PriceProvider:
    """A fresh adapter for `name`; unknown names are a configuration error, not a silent Yahoo."""
    if name == "yahoo":
        from instilens.ingestion.prices.yahoo import YahooProvider

        return YahooProvider()
    if name == "matriks":
        from instilens.ingestion.prices.matriks import MatriksProvider

        return MatriksProvider()
    raise ValueError(f"unknown price provider {name!r} (one of {', '.join(PROVIDERS)})")


# One instance per name so a provider can remember its last successful call (status.last_tick_at / error).
_instances: dict[str, PriceProvider] = {}
_fallback_logged: set[str] = set()


def _instance(name: str) -> PriceProvider:
    if name not in _instances:
        _instances[name] = build_provider(name)
    return _instances[name]


def resolve_provider() -> PriceProvider:
    """The configured provider (`price_provider` runtime setting) when it is configured, else Yahoo.
    The fallback is logged once per name, not on every call — the feed asks every few seconds."""
    name = settings.price_provider or DEFAULT_PROVIDER
    try:
        provider = _instance(name)
    except ValueError as exc:
        if name not in _fallback_logged:
            log.warning("price_provider: %s; using %s", exc, DEFAULT_PROVIDER)
            _fallback_logged.add(name)
        return _instance(DEFAULT_PROVIDER)
    status = provider.status()
    if status.configured:
        return provider
    if name not in _fallback_logged:
        log.warning("price_provider=%s is not configured (%s); using %s", name, status.note or status.error or "no credentials", DEFAULT_PROVIDER)
        _fallback_logged.add(name)
    return _instance(DEFAULT_PROVIDER)


def reset() -> None:
    """Forget cached instances and the once-only fallback log (tests)."""
    _instances.clear()
    _fallback_logged.clear()
