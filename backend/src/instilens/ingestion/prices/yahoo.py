"""Yahoo Finance (yfinance) price provider. Delayed (~15 min), unofficial, free — fine for development and
the beta; production plugs a licensed vendor into the same `PriceProvider` contract (see provider.py).

Symbols: BIST tickers become `ASELS.IS`; US tickers are used as-is. Unknown/illiquid symbols (or unmapped
CUSIP placeholders) are skipped, never guessed. `fetch_frame` / `fetch_history` are the only two functions
that talk to Yahoo — tests monkeypatch them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal

from instilens.ingestion.prices.provider import Bar, ProviderStatus, ProviderUnavailable, Tick

log = logging.getLogger("instilens.prices.yahoo")

NOTE = "Yahoo Finance — unofficial, ~15 min delayed"
BATCH = 50  # tickers per yf.download call


def yahoo_symbol(market: str, symbol: str) -> str | None:
    if not symbol.isalpha() and market == "US":
        return None  # CUSIP placeholder
    return f"{symbol}.IS" if market == "TR" else symbol


def fetch_frame(tickers: list[str]):
    """Daily bars for the last month (the current, partial session included while trading). A month rather
    than a handful of days so a multi-day exchange holiday still leaves two bars to compute the change from."""
    import yfinance as yf

    return yf.download(tickers, period="1mo", interval="1d", auto_adjust=False, progress=False, group_by="ticker", threads=True)


def fetch_history(tickers: list[str], start: date):
    """Daily OHLCV bars from `start` for up to BATCH tickers."""
    import yfinance as yf

    return yf.download(tickers, start=start.isoformat(), auto_adjust=False, progress=False, group_by="ticker", threads=True)


def _ticker_frame(frame, ticker: str):
    """The Open/High/Low/Close/Volume table of one ticker: its group in a grouped multi-ticker frame (either
    level order yfinance has used), or the frame itself when it is flat (single ticker). None when the grouped
    frame lacks the ticker — never a sub-frame of some other ticker."""
    columns = getattr(frame, "columns", None)
    if getattr(columns, "nlevels", 1) <= 1:
        return frame
    for level in (0, 1):
        try:
            if ticker in columns.get_level_values(level):
                return frame.xs(ticker, axis=1, level=level)
        except (KeyError, TypeError, AttributeError):
            continue
    return None


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


def _money(value) -> Decimal | None:
    """A frame cell as 4-decimal Money; None for NaN/missing (Yahoo leaves holes in thin symbols)."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else Decimal(str(round(f, 4)))


def _volume(value) -> int | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f else int(f)


def parse_bars(frame, ticker: str, symbol: str) -> list[Bar]:
    """OHLCV bars of one ticker, oldest first; rows without a close are dropped. `symbol` is what the caller asked for."""
    table = _ticker_frame(frame, ticker)
    if table is None:
        return []
    try:
        closes = table["Close"]
    except (KeyError, TypeError):
        return []
    if getattr(closes, "ndim", 0) != 1:
        return []
    out: list[Bar] = []
    for ts, row in table.iterrows():
        close = _money(row.get("Close"))
        if close is None:
            continue
        out.append(Bar(symbol, ts.date(), _money(row.get("Open")), _money(row.get("High")), _money(row.get("Low")), close, _volume(row.get("Volume"))))
    return out


class YahooProvider:
    """`connected` means the last call to Yahoo was answered *with data*; there is no session to hold open.
    yfinance swallows per-ticker network errors (logged, never raised) and hands back a frame over an empty index,
    so a frame that yields nothing is the only shape a real outage takes — it is reported as one."""

    name = "yahoo"

    def __init__(self) -> None:
        self._connected = False
        self._last_tick_at: datetime | None = None
        self._error: str | None = None

    def _fail(self, what: str, exc: Exception) -> ProviderUnavailable:
        self._connected, self._error = False, f"{what}: {exc}"[:200]
        return ProviderUnavailable(f"yahoo {what}: {exc}")

    def quotes(self, tickers: list[str]) -> dict[str, Tick]:
        try:
            frame = fetch_frame(tickers)
        except Exception as exc:  # noqa: BLE001 — any yfinance/network failure is "unavailable" to the caller
            raise self._fail("fetch", exc) from exc
        if frame is None:
            raise self._fail("fetch", RuntimeError("no data returned"))
        ticks: dict[str, Tick] = {}
        for tk in tickers:
            try:
                parsed = parse_quote(frame, tk)
            except Exception as exc:  # noqa: BLE001 — an unexpected frame shape drops this ticker, not the payload
                log.warning("quotes: cannot parse %s: %s", tk, exc)
                continue
            if parsed is None:
                continue
            ticks[tk] = Tick(tk, parsed.price, parsed.change_pct, parsed.bar_date, None)  # at=None: only the fetch time is known
        if not ticks:
            raise self._fail("quotes", RuntimeError(f"no data for any of {len(tickers)} tickers"))
        self._connected, self._error, self._last_tick_at = True, None, datetime.now(UTC)
        return ticks

    def daily_bars(self, market: str, symbols: list[str], start: date) -> list[Bar]:
        mapped = [(s, yahoo_symbol(market, s)) for s in symbols]
        mapped = [(s, tk) for s, tk in mapped if tk]
        if not mapped:
            return []  # nothing Yahoo could be asked for: not a call, not a verdict on the connection
        bars: list[Bar] = []
        batches, empty = 0, 0  # `empty`: batches that came back without a frame (yfinance's shape for a swallowed network error)
        for i in range(0, len(mapped), BATCH):
            chunk = mapped[i : i + BATCH]
            batches += 1
            try:
                frame = fetch_history([tk for _, tk in chunk], start)
            except Exception as exc:  # noqa: BLE001
                raise self._fail("history", exc) from exc
            if frame is None or getattr(frame, "empty", True):
                empty += 1
                log.warning("history: empty frame for %s tickers (%s…)", len(chunk), chunk[0][1])
                continue
            for symbol, tk in chunk:
                bars.extend(parse_bars(frame, tk, symbol))
        if not bars:
            raise self._fail("history", RuntimeError(f"no bars for any of {len(mapped)} tickers"))
        self._connected, self._last_tick_at = True, datetime.now(UTC)
        self._error = f"history: {empty} of {batches} batches came back empty" if empty else None
        return bars

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.name, configured=True, connected=self._connected, delay="delayed", last_tick_at=self._last_tick_at, error=self._error, note=NOTE)
