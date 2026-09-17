"""Yahoo Finance (yfinance) fundamentals provider: the three statements and the `info` metrics. Free, unofficial —
fine for development and the beta; a licensed vendor plugs into the same `FundamentalsProvider` contract.

Symbols follow the price adapter (`ASELS.IS`, `AAPL`; a CUSIP placeholder maps to nothing and is skipped, never
guessed). `fetch_statement_frames` and `fetch_info` are the only two functions that talk to Yahoo — tests
monkeypatch them with DataFrames shaped exactly like yfinance's: row labels are Yahoo's line-item names ("Total
Revenue", "Stockholders Equity", ...), columns are period-end Timestamps, newest first. Both go through `_yfinance()`,
which switches off yfinance's exception hiding: with the default (`debug.hide_exceptions=True`) a rate limit or a
transport error inside a statements fetch is logged by yfinance and comes back as an empty frame, indistinguishable
from a ticker it has nothing for — and a run being throttled would keep asking for every remaining ticker.

Two currencies per ticker: `financialCurrency` is the filer's reporting currency (statements, TTM revenue / EBITDA /
net income); `currency` is the listing currency market cap, enterprise value, the 52-week range and trailing EPS are
quoted in. THYAO reports in USD and trades in TRY, a US ADR the other way round — the snapshot carries both.

Every value is what Yahoo reports; a canonical key whose line is absent stays None. The single exception is
`free_cf`: when "Free Cash Flow" is missing but "Operating Cash Flow" and "Capital Expenditure" are both present,
it is their sum (Yahoo prints capex as a negative number) — the same arithmetic Yahoo applies to print the line.
Analyst fields (recommendationKey / recommendationMean / target prices) are never read: descriptive product, SPK rule.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any

from instilens.ingestion.fundamentals.provider import (
    SNAPSHOT_KEYS,
    STATEMENT_KINDS,
    MetricsSnapshot,
    PeriodKind,
    ProviderStatus,
    ProviderUnavailable,
    Statement,
    StatementKind,
)
from instilens.ingestion.prices.yahoo import yahoo_symbol

log = logging.getLogger("instilens.fundamentals.yahoo")

NOTE = "Yahoo Finance — reported statements and trailing metrics, unofficial"
INFO_TTL_S = 60  # the last ticker's `info` is remembered this long, so statements() and snapshot() of one refresh share a fetch

# The market's currency, assumed when Yahoo states no `financialCurrency` / `currency`; the Statement/MetricsSnapshot then says so.
MARKET_CURRENCY: dict[str, str] = {"TR": "TRY", "US": "USD"}

# Canonical key → yfinance row labels, tried in order; the first label that carries a value wins. Alternates are
# the same line under the label Yahoo prints for some filers (banks, holdings), never a different item:
#   equity           "Common Stock Equity" when "Stockholders Equity" is absent
#   net_income       "Net Income Common Stockholders" when "Net Income" is absent
#   operating_income "Total Operating Income As Reported" when "Operating Income" is absent
#   operating_cf     "Cash Flow From Continuing Operating Activities" when "Operating Cash Flow" is absent
#   cost_of_revenue  "Reconciled Cost Of Revenue" when "Cost Of Revenue" is absent
#   revenue          "Operating Revenue" when "Total Revenue" is absent
#   interest_expense "Interest Expense Non Operating" when "Interest Expense" is absent
#   dividends_paid   "Common Stock Dividend Paid" when "Cash Dividends Paid" is absent
#   share_repurchase "Common Stock Payments" when "Repurchase Of Capital Stock" is absent
LABELS: dict[StatementKind, dict[str, tuple[str, ...]]] = {
    "income": {
        "revenue": ("Total Revenue", "Operating Revenue"),
        "cost_of_revenue": ("Cost Of Revenue", "Reconciled Cost Of Revenue"),
        "gross_profit": ("Gross Profit",),
        "operating_income": ("Operating Income", "Total Operating Income As Reported"),
        "ebitda": ("EBITDA",),
        "pretax_income": ("Pretax Income",),
        "net_income": ("Net Income", "Net Income Common Stockholders"),
        "eps_diluted": ("Diluted EPS",),
        "interest_expense": ("Interest Expense", "Interest Expense Non Operating"),
    },
    "balance": {
        "total_assets": ("Total Assets",),
        "total_liabilities": ("Total Liabilities Net Minority Interest",),
        "equity": ("Stockholders Equity", "Common Stock Equity"),
        "total_debt": ("Total Debt",),
        "cash": ("Cash And Cash Equivalents",),
        "current_assets": ("Current Assets",),
        "current_liabilities": ("Current Liabilities",),
    },
    "cashflow": {
        "operating_cf": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
        "capex": ("Capital Expenditure",),
        "free_cf": ("Free Cash Flow",),
        "dividends_paid": ("Cash Dividends Paid", "Common Stock Dividend Paid"),
        "share_repurchase": ("Repurchase Of Capital Stock", "Common Stock Payments"),
    },
}

# Snapshot field → `info` key. `dividend_yield` reads trailingAnnualDividendYield (dividends of the last twelve
# months / price, always a fraction); when that is 0 or absent it falls back to `dividendYield`, see parse_snapshot.
INFO_KEYS: dict[str, str] = {
    "market_cap": "marketCap",
    "enterprise_value": "enterpriseValue",
    "pe": "trailingPE",
    "forward_pe": "forwardPE",
    "price_to_book": "priceToBook",
    "price_to_sales": "priceToSalesTrailing12Months",
    "ev_to_ebitda": "enterpriseToEbitda",
    "profit_margin": "profitMargins",
    "operating_margin": "operatingMargins",
    "return_on_assets": "returnOnAssets",
    "return_on_equity": "returnOnEquity",
    "revenue_ttm": "totalRevenue",
    "ebitda_ttm": "ebitda",
    "net_income_ttm": "netIncomeToCommon",
    "eps_ttm": "trailingEps",
    "dividend_yield": "trailingAnnualDividendYield",
    "payout_ratio": "payoutRatio",
    "beta": "beta",
    "week52_high": "fiftyTwoWeekHigh",
    "week52_low": "fiftyTwoWeekLow",
    "shares_outstanding": "sharesOutstanding",
    "float_shares": "floatShares",
    "short_percent_of_float": "shortPercentOfFloat",
}
assert set(INFO_KEYS) == set(SNAPSHOT_KEYS)
# Yahoo states these as fractions (0.123); the contract carries percentages (12.3).
FRACTION_KEYS = frozenset({"profit_margin", "operating_margin", "return_on_assets", "return_on_equity", "dividend_yield", "payout_ratio", "short_percent_of_float"})


def _yfinance():
    """yfinance with `debug.hide_exceptions` off, so a failed fetch raises instead of coming back as an empty frame
    (see the module docstring). The flag is process-wide; the price adapter goes through `yf.download`, which catches
    per ticker whatever the flag says, so nothing changes there."""
    import yfinance as yf

    yf.config.debug.hide_exceptions = False
    return yf


def fetch_statement_frames(ticker: str, period: PeriodKind) -> dict[StatementKind, Any]:
    """The three statement frames of one ticker (annual or quarterly), as yfinance hands them out. Each is its own
    Yahoo call: one Yahoo has no answer for (the quarterly cash flow of most BIST names) is None and the other two
    still count; an outage or anything else propagates."""
    t = _yfinance().Ticker(ticker)
    prefix = "quarterly_" if period == "quarterly" else ""
    out: dict[StatementKind, Any] = {}
    for kind, attr in (("income", "income_stmt"), ("balance", "balance_sheet"), ("cashflow", "cashflow")):
        try:
            out[kind] = getattr(t, prefix + attr)
        except Exception as exc:
            if _is_outage(exc) or not _is_nothing(exc):
                raise
            out[kind] = None
    return out


def fetch_info(ticker: str) -> dict:
    """yfinance's `info` dict (summary detail + key statistics + financial data) of one ticker."""
    return _yfinance().Ticker(ticker).info or {}


def _number(value) -> float | None:
    """A frame cell / info value as float; None for NaN, missing or non-numeric (Yahoo leaves holes)."""
    if isinstance(value, bool):
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if f != f or f in (float("inf"), float("-inf")) else f


def _period_end(column) -> date | None:
    """A frame column as its period-end date; None for a column that is not a date (a "TTM" column, say)."""
    if isinstance(column, datetime):  # pandas Timestamp is a datetime
        return column.date()
    if isinstance(column, date):
        return column
    try:
        return date.fromisoformat(str(column)[:10])
    except ValueError:
        return None


def _rows(frame) -> dict[str, Any]:
    """Row label → row; the first occurrence wins when Yahoo repeats a label."""
    out: dict[str, Any] = {}
    for label, row in frame.iterrows():
        out.setdefault(str(label), row)
    return out


def parse_statements(frames: dict[StatementKind, Any], currency: str | None, currency_assumed: bool = False) -> list[Statement]:
    """Statements from yfinance frames: one per (kind, period-end column), newest first within each kind. Periods
    where every canonical line is missing are dropped; NaN becomes None; free_cf falls back to operating_cf + capex."""
    out: list[Statement] = []
    for kind in STATEMENT_KINDS:
        frame = frames.get(kind)
        if frame is None or getattr(frame, "empty", True):
            continue
        rows = _rows(frame)
        parsed: list[Statement] = []
        for column in frame.columns:
            end = _period_end(column)
            if end is None:
                continue
            items: dict[str, float | None] = {}
            for key, labels in LABELS[kind].items():
                value = None
                for label in labels:
                    if label in rows:
                        value = _number(rows[label][column])
                        if value is not None:
                            break
                items[key] = value
            if kind == "cashflow" and items["free_cf"] is None and items["operating_cf"] is not None and items["capex"] is not None:
                items["free_cf"] = items["operating_cf"] + items["capex"]
            if all(v is None for v in items.values()):
                continue
            parsed.append(Statement(kind, end, currency, items, currency_assumed))
        out.extend(sorted(parsed, key=lambda s: s.period_end, reverse=True))
    return out


def parse_snapshot(info: dict, as_of: date, currency: str | None, currency_assumed: bool = False, quote_currency: str | None = None, quote_currency_assumed: bool = False) -> MetricsSnapshot | None:
    """The metrics snapshot from an `info` dict; None when Yahoo states none of the fields (an unknown ticker's
    `info` is near-empty). Fractions become percentages; everything else is taken as printed. `currency` is the
    reporting currency (statements, TTM lines), `quote_currency` the listing currency (market cap, EV, 52w, EPS)."""
    fields: dict[str, float | None] = {}
    for key, info_key in INFO_KEYS.items():
        value = _number(info.get(info_key))
        fields[key] = round(value * 100.0, 4) if value is not None and key in FRACTION_KEYS else value
    # Yahoo prints trailingAnnualDividendYield=0.0 wherever it lacks the dividend history (most BIST names, ADRs) while
    # its dividendYield is the real figure (GARAN: 0.0 vs 4.12 with an 18 % payout). A zero there is not a reported
    # yield: fall back to dividendYield, which Yahoo has printed as a percentage since 2025 — taken as-is. Neither
    # stated → None, never 0.
    if not fields["dividend_yield"]:
        fields["dividend_yield"] = _number(info.get("dividendYield"))
    if all(v is None for v in fields.values()):
        return None
    return MetricsSnapshot(as_of=as_of, currency=currency, quote_currency=quote_currency, currency_assumed=currency_assumed, quote_currency_assumed=quote_currency_assumed, **fields)


def _http_status(exc: Exception) -> int | None:
    """The status code behind a requests / curl_cffi HTTPError (both carry `.response`); None for anything else."""
    code = getattr(getattr(exc, "response", None), "status_code", None)
    return code if isinstance(code, int) else None


def _is_outage(exc: Exception) -> bool:
    """Yahoo's rate limit (YFRateLimitError, HTTP 429), a server error (5xx) and transport failures (requests /
    curl_cffi errors are OSErrors) are outages: the whole run stops. A 4xx for one ticker is not — a delisted
    ticker's 404 must not end the batch."""
    from yfinance.exceptions import YFRateLimitError

    if isinstance(exc, YFRateLimitError):
        return True
    status = _http_status(exc)
    if status is not None:
        return status == 429 or status >= 500
    return isinstance(exc, OSError)


def _is_nothing(exc: Exception) -> bool:
    """Yahoo's ways of saying it has no such data, once exceptions are not hidden: a 4xx for the ticker (404 for an
    unknown symbol), a missing or delisted ticker (YFTickerMissingError, YFTzMissingError) or an empty
    fundamentals-timeseries answer (ETFs, thin names) — all YFExceptions short of the rate limit."""
    from yfinance.exceptions import YFException, YFRateLimitError

    status = _http_status(exc)
    if status is not None:
        return 400 <= status < 500 and status != 429
    return isinstance(exc, YFException) and not isinstance(exc, YFRateLimitError)


class YahooFundamentals:
    """`connected` means the last call to Yahoo was answered *with data*; there is no session to hold open. An
    outage (transport error, rate limit, server error) is ProviderUnavailable and lands in status(); a ticker Yahoo
    simply has nothing for — a 404, its own missing-ticker errors, an empty fundamentals answer — yields an empty
    answer and leaves the status alone: thin BIST names and ETFs look like that. Anything else (odd JSON, say) is
    the ticker's own problem and propagates as-is."""

    name = "yahoo"

    def __init__(self) -> None:
        self._connected = False
        self._last_tick_at: datetime | None = None
        self._error: str | None = None
        self._info: tuple[str, datetime, dict] | None = None  # (ticker, fetched at, info) of the last `info` call

    def _fail(self, what: str, exc: Exception) -> ProviderUnavailable:
        self._connected, self._error = False, f"{what}: {exc}"[:200]
        return ProviderUnavailable(f"yahoo {what}: {exc}")

    def _guard(self, what: str, call: Callable[[], Any], nothing: Any = None) -> Any:
        """One Yahoo call: an outage becomes ProviderUnavailable (remembered in status()), "no such data" becomes
        `nothing`, anything else propagates as-is for the caller's per-symbol handling."""
        try:
            return call()
        except Exception as exc:
            if _is_outage(exc):
                raise self._fail(what, exc) from exc
            if _is_nothing(exc):
                log.debug("%s: nothing at yahoo: %s: %s", what, type(exc).__name__, str(exc)[:120])
                return nothing
            raise

    def _ticker_info(self, ticker: str) -> dict:
        now = datetime.now(UTC)
        if self._info is not None and self._info[0] == ticker and (now - self._info[1]).total_seconds() < INFO_TTL_S:
            return self._info[2]
        info = dict(self._guard("info", lambda: fetch_info(ticker), nothing={}) or {})
        self._info = (ticker, now, info)
        return info

    @staticmethod
    def _currency(market: str, info: dict, key: str = "financialCurrency") -> tuple[str | None, bool]:
        """A currency as Yahoo states it — `financialCurrency` (reporting) by default, `currency` (listing) on
        request; the market default, flagged, when it does not."""
        stated = info.get(key)
        if isinstance(stated, str) and stated.strip():
            return stated.strip()[:3], False
        fallback = MARKET_CURRENCY.get(market)
        return fallback, fallback is not None

    def _answered(self) -> None:
        self._connected, self._error, self._last_tick_at = True, None, datetime.now(UTC)

    def statements(self, market: str, symbol: str, period: PeriodKind) -> list[Statement]:
        ticker = yahoo_symbol(market, symbol)
        if ticker is None:
            return []  # nothing Yahoo could be asked for: not a call, not a verdict on the connection
        currency, assumed = self._currency(market, self._ticker_info(ticker))
        frames = self._guard("statements", lambda: fetch_statement_frames(ticker, period), nothing={})
        out = parse_statements(frames, currency, assumed)
        if out:
            self._answered()
        return out

    def snapshot(self, market: str, symbol: str) -> MetricsSnapshot | None:
        ticker = yahoo_symbol(market, symbol)
        if ticker is None:
            return None
        info = self._ticker_info(ticker)
        currency, assumed = self._currency(market, info)
        quote_currency, quote_assumed = self._currency(market, info, "currency")
        snap = parse_snapshot(info, datetime.now(UTC).date(), currency, assumed, quote_currency, quote_assumed)
        if snap is not None:
            self._answered()
        return snap

    def status(self) -> ProviderStatus:
        return ProviderStatus(self.name, configured=True, connected=self._connected, delay="eod", last_tick_at=self._last_tick_at, error=self._error, note=NOTE)
