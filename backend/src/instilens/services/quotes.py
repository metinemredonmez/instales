"""Header quotes (USD/TRY, EUR/TRY, BIST 100, S&P 500) and market-hours state.

Prices come from Yahoo Finance through the existing yfinance dependency — delayed and unofficial, the same
feed `ingestion/prices/yahoo.py` uses. One in-process cache (60 s) shields Yahoo from a page-load storm; the
last good value per ticker is remembered so an outage shows *stale* numbers flagged as such, never a made-up
one and never an old number presented as fresh. A ticker Yahoo cannot answer and we have never seen is
simply omitted.

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

log = logging.getLogger(__name__)

CACHE_SECONDS = 60


@dataclass(frozen=True)
class QuoteSpec:
    key: str
    ticker: str  # Yahoo symbol
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


def fetch_frame(tickers: list[str]):
    """Daily bars for the last month (the current, partial session included while trading). A month rather
    than a handful of days so a multi-day exchange holiday still leaves two bars to compute the change from.
    Tests monkeypatch this; it is the only place that talks to Yahoo."""
    import yfinance as yf

    return yf.download(tickers, period="1mo", interval="1d", auto_adjust=False, progress=False, group_by="ticker", threads=True)


def _closes(frame, ticker: str):
    """Close series for one ticker: the (ticker, field) / (field, ticker) column of a grouped multi-ticker
    frame, or the plain "Close" column of a single-ticker frame. Only a one-dimensional series qualifies —
    a grouped frame that lacks the ticker yields None rather than a whole sub-frame."""
    grouped = getattr(getattr(frame, "columns", None), "nlevels", 1) > 1
    getters = (lambda: frame[ticker]["Close"], lambda: frame["Close"][ticker]) if grouped else (lambda: frame["Close"],)
    for getter in getters:
        try:
            series = getter()
        except (KeyError, TypeError, AttributeError):
            continue
        if getattr(series, "ndim", 0) == 1:
            return series.dropna()
    return None


@dataclass(frozen=True)
class ParsedQuote:
    price: float
    change_pct: float | None  # versus the previous bar's close; None with a single bar
    bar_date: date | None  # session the price belongs to (the last bar's index), when the index is datetime-like


def parse_quote(frame, ticker: str) -> ParsedQuote | None:
    """Last price, % change versus the previous close and the bar's date; None when the frame has no usable close."""
    closes = _closes(frame, ticker)
    if closes is None or closes.empty:
        return None
    price = float(closes.iloc[-1])
    if price <= 0:
        return None
    last = closes.index[-1]
    bar_date = last.date() if hasattr(last, "date") and callable(last.date) else None
    if len(closes) >= 2 and float(closes.iloc[-2]) > 0:
        prev = float(closes.iloc[-2])
        return ParsedQuote(price, round((price / prev - 1.0) * 100.0, 2), bar_date)
    return ParsedQuote(price, None, bar_date)


_lock = threading.Lock()
_cache: dict[str, Any] = {"fetched_at": None, "quotes": {}}  # fetched_at: datetime | None
_last_good: dict[str, dict[str, Any]] = {}


def reset() -> None:
    with _lock:
        _cache["fetched_at"], _cache["quotes"] = None, {}
        _last_good.clear()


def _refresh(now: datetime) -> None:
    tickers = [q.ticker for q in QUOTES]
    try:
        frame = fetch_frame(tickers)
    except Exception as exc:  # noqa: BLE001 — Yahoo outage: keep last-good, mark stale below
        log.warning("quotes: yahoo fetch failed: %s", exc)
        frame = None
    fresh: dict[str, dict[str, Any]] = {}
    for spec in QUOTES:
        if frame is None:
            continue
        try:
            parsed = parse_quote(frame, spec.ticker)
        except Exception as exc:  # noqa: BLE001 — an unexpected frame shape drops this ticker, not the payload
            log.warning("quotes: cannot parse %s: %s", spec.ticker, exc)
            continue
        if parsed is None:
            continue
        # updated_at is when Yahoo last confirmed the price; bar_date is the session it was printed in, so a
        # weekend read of Friday's close is not mistaken for a Saturday print.
        fresh[spec.key] = {"key": spec.key, "label": spec.label, "price": round(parsed.price, spec.decimals), "change_pct": parsed.change_pct,
                           "currency": spec.currency, "updated_at": now.isoformat(), "decimals": spec.decimals,
                           "bar_date": parsed.bar_date.isoformat() if parsed.bar_date else None}
    _last_good.update(fresh)
    _cache["fetched_at"], _cache["quotes"] = now, fresh


def snapshot(now: datetime | None = None) -> dict[str, Any]:
    """The /quotes payload. Refreshes from Yahoo at most once per CACHE_SECONDS; concurrent callers share one fetch."""
    now = now or datetime.now(UTC)
    with _lock:
        fetched_at = _cache["fetched_at"]
        if fetched_at is None or (now - fetched_at).total_seconds() >= CACHE_SECONDS:
            _refresh(now)
        fresh = _cache["quotes"]
        quotes = []
        for spec in QUOTES:
            if spec.key in fresh:
                quotes.append(dict(fresh[spec.key]))
            elif spec.key in _last_good:
                quotes.append({**_last_good[spec.key], "stale": True})
    return {"as_of": now.isoformat(), "quotes": quotes, "markets": markets(now)}
