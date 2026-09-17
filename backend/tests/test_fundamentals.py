"""Fundamentals (Faz 3): the Yahoo adapter behind monkeypatched frames, the refresh loader, the derived ratios,
the API/stock_detail shapes, the AI tools, the migration and the refresh universe. Network-free throughout."""

from datetime import UTC, date, datetime, timedelta

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from instilens.config import settings
from instilens.domain.enums import Market
from instilens.domain.models import (
    Disclosure,
    Fundamental,
    FundamentalSnapshot,
    Instrument,
    PortfolioSnapshot,
    PositionChange,
    TransactionEvent,
    Watchlist,
    WatchlistItem,
)
from instilens.ingestion.fundamentals import provider, yahoo
from instilens.ingestion.fundamentals.provider import (
    CANONICAL_KEYS,
    SNAPSHOT_KEYS,
    ProviderUnavailable,
    Statement,
)
from instilens.services import analytics, auth, fundamentals
from instilens.services.entities import EntityResolver


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Provider instances must not leak between tests; the setting is always the one adapter that exists."""
    monkeypatch.setattr(settings, "fundamentals_provider", "yahoo")
    provider.reset()
    yield
    provider.reset()


def _frame(rows: dict[str, list], ends: list[str]) -> pd.DataFrame:
    """A yfinance statement frame: row labels = line items, columns = period-end Timestamps (newest first)."""
    return pd.DataFrame(rows, index=[pd.Timestamp(e) for e in ends]).T


NAN = float("nan")
ANNUAL_ENDS = ["2025-12-31", "2024-12-31", "2023-12-31"]
ANNUAL = {
    "income": _frame({
        "Total Revenue": [1000.0, 800.0, 640.0], "Cost Of Revenue": [600.0, 500.0, 400.0], "Gross Profit": [400.0, 300.0, 240.0],
        "Operating Income": [200.0, 150.0, NAN], "EBITDA": [250.0, 190.0, 150.0], "Pretax Income": [190.0, 140.0, 100.0],
        "Net Income": [150.0, 100.0, 80.0], "Diluted EPS": [1.5, 1.0, 0.8], "Interest Expense": [10.0, 12.0, 15.0],
    }, ANNUAL_ENDS),
    "balance": _frame({
        "Total Assets": [2000.0, 1800.0, 1600.0], "Total Liabilities Net Minority Interest": [1200.0, 1100.0, 1000.0],
        "Common Stock Equity": [800.0, 700.0, 600.0],  # the alternate label: "Stockholders Equity" absent for this filer
        "Total Debt": [400.0, 350.0, 300.0], "Cash And Cash Equivalents": [100.0, 90.0, 80.0],
        "Current Assets": [900.0, 800.0, 700.0], "Current Liabilities": [500.0, 450.0, 400.0],
    }, ANNUAL_ENDS),
    "cashflow": _frame({
        "Operating Cash Flow": [300.0, 250.0, 200.0], "Capital Expenditure": [-100.0, -80.0, -60.0],
        "Free Cash Flow": [NAN, 170.0, 140.0],  # 2025: absent → operating_cf + capex; earlier years: as printed
        "Cash Dividends Paid": [-50.0, -40.0, -30.0], "Repurchase Of Capital Stock": [NAN, NAN, NAN],
    }, ANNUAL_ENDS),
}
QUARTER_ENDS = ["2025-06-30", "2025-03-31", "2024-12-31", "2024-09-30", "2024-06-30"]
QUARTERLY = {
    "income": _frame({"Total Revenue": [300.0, 250.0, 280.0, 220.0, 200.0], "Gross Profit": [120.0, 100.0, 110.0, 90.0, 80.0],
                      "Net Income": [45.0, 30.0, 40.0, 25.0, 20.0]}, QUARTER_ENDS),
    "balance": _frame({"Stockholders Equity": [820.0, 790.0, 800.0, 760.0, 720.0], "Total Debt": [410.0, 400.0, 400.0, 380.0, 360.0]}, QUARTER_ENDS),
    "cashflow": pd.DataFrame(),  # nothing quarterly for the cash flow: an empty frame, as yfinance hands it out
}
INFO = {
    "financialCurrency": "TRY", "currency": "TRY", "marketCap": 5_000_000_000.0, "enterpriseValue": 5_400_000_000.0, "trailingPE": 12.5, "forwardPE": 10.0,
    "priceToBook": 2.1, "profitMargins": 0.15, "returnOnEquity": 0.2, "trailingAnnualDividendYield": 0.021, "dividendYield": 2.1,
    "payoutRatio": 0.3, "sharesOutstanding": 456_000_000, "floatShares": 200_000_000.0, "beta": 1.1, "fiftyTwoWeekHigh": 99.5,
    # analyst fields Yahoo also sends — never read (SPK rule)
    "recommendationKey": "buy", "recommendationMean": 1.8, "targetMeanPrice": 140.0,
}


def _fake_yahoo(monkeypatch, frames=None, info=None):
    """Both seams answer every ticker with the same fixtures; returns the list of (ticker, period) statement fetches."""
    asked: list[tuple[str, str]] = []

    def fetch_frames(ticker, period):
        asked.append((ticker, period))
        return (frames or {"annual": ANNUAL, "quarterly": QUARTERLY})[period]

    monkeypatch.setattr(yahoo, "fetch_statement_frames", fetch_frames)
    monkeypatch.setattr(yahoo, "fetch_info", lambda ticker: dict(INFO if info is None else info))
    return asked


# --- registry -------------------------------------------------------------------------------------


def test_registry_builds_yahoo_and_falls_back_once(caplog, monkeypatch):
    assert provider.PROVIDERS == ("yahoo",) and isinstance(provider.build_provider("yahoo"), yahoo.YahooFundamentals)
    with pytest.raises(ValueError):
        provider.build_provider("bloomberg")
    monkeypatch.setattr(settings, "fundamentals_provider", "bloomberg")
    with caplog.at_level("WARNING", logger="instilens.fundamentals"):
        first = provider.resolve_provider()
        assert provider.resolve_provider() is first
    assert isinstance(first, yahoo.YahooFundamentals)
    assert len([r for r in caplog.records if "unknown fundamentals provider" in r.getMessage()]) == 1
    st = first.status()
    assert st.name == "yahoo" and st.configured and not st.connected and st.delay == "eod" and st.error is None and st.note == yahoo.NOTE
    assert st.as_dict()["last_tick_at"] is None


# --- label mapping --------------------------------------------------------------------------------


def test_labels_map_to_canonical_keys_with_alternates_and_nan():
    out = yahoo.parse_statements(ANNUAL, "TRY")
    by = {(s.kind, s.period_end): s for s in out}
    assert [(s.kind, s.period_end.isoformat()) for s in out] == [(k, e) for k in ("income", "balance", "cashflow") for e in ANNUAL_ENDS]  # newest first per kind
    for s in out:
        assert tuple(s.items) == CANONICAL_KEYS[s.kind] and s.currency == "TRY" and s.currency_assumed is False
    income = by[("income", date(2025, 12, 31))].items
    assert income == {"revenue": 1000.0, "cost_of_revenue": 600.0, "gross_profit": 400.0, "operating_income": 200.0, "ebitda": 250.0,
                      "pretax_income": 190.0, "net_income": 150.0, "eps_diluted": 1.5, "interest_expense": 10.0}
    assert by[("income", date(2023, 12, 31))].items["operating_income"] is None  # NaN → None, never 0
    balance = by[("balance", date(2025, 12, 31))].items
    assert balance["equity"] == 800.0 and balance["total_liabilities"] == 1200.0 and balance["cash"] == 100.0  # alternate label for equity
    assert all(isinstance(v, float) for v in balance.values())

    # Every documented alternate is honoured when the primary label is missing; an unknown label maps to nothing.
    frames = {
        "income": _frame({"Operating Revenue": [10.0], "Reconciled Cost Of Revenue": [4.0], "Total Operating Income As Reported": [3.0],
                          "Net Income Common Stockholders": [2.0], "Interest Expense Non Operating": [0.5], "Some Other Line": [99.0]}, ["2025-12-31"]),
        "cashflow": _frame({"Cash Flow From Continuing Operating Activities": [5.0], "Common Stock Dividend Paid": [-1.0], "Common Stock Payments": [-2.0]}, ["2025-12-31"]),
    }
    alt = {s.kind: s.items for s in yahoo.parse_statements(frames, "USD")}
    assert alt["income"] == {"revenue": 10.0, "cost_of_revenue": 4.0, "gross_profit": None, "operating_income": 3.0, "ebitda": None,
                             "pretax_income": None, "net_income": 2.0, "eps_diluted": None, "interest_expense": 0.5}
    assert alt["cashflow"] == {"operating_cf": 5.0, "capex": None, "free_cf": None, "dividends_paid": -1.0, "share_repurchase": -2.0}
    # The primary label wins when both are present but yields to the alternate when its own cell is NaN.
    both = _frame({"Stockholders Equity": [700.0, NAN], "Common Stock Equity": [650.0, 600.0]}, ["2025-12-31", "2024-12-31"])
    eq = [s.items["equity"] for s in yahoo.parse_statements({"balance": both}, "TRY")]
    assert eq == [700.0, 600.0]

    # A column that is not a period end (a TTM column), a period with no canonical line at all, a duplicated label.
    odd = pd.DataFrame({pd.Timestamp("2025-12-31"): [1.0, 2.0, 7.0], "TTM": [9.0, 9.0, 9.0], pd.Timestamp("2024-12-31"): [NAN, NAN, 3.0]},
                       index=["Total Revenue", "Total Revenue", "Unmapped"])
    parsed = yahoo.parse_statements({"income": odd}, "TRY")
    assert [(s.period_end.isoformat(), s.items["revenue"]) for s in parsed] == [("2025-12-31", 1.0)]  # first label wins; 2024 dropped (nothing canonical)
    assert yahoo.parse_statements({"income": None, "balance": pd.DataFrame()}, "TRY") == []


def test_free_cash_flow_falls_back_to_operating_plus_capex_only_when_both_are_present():
    cf = {s.period_end.isoformat(): s.items for s in yahoo.parse_statements({"cashflow": ANNUAL["cashflow"]}, "TRY")}
    assert cf["2025-12-31"]["free_cf"] == 200.0  # 300 + (-100): the one computed value, documented in the adapter
    assert cf["2024-12-31"]["free_cf"] == 170.0  # printed by Yahoo: taken as-is even though 250 - 80 = 170 as well
    assert cf["2025-12-31"]["share_repurchase"] is None
    partial = _frame({"Operating Cash Flow": [300.0, NAN], "Capital Expenditure": [NAN, -80.0]}, ["2025-12-31", "2024-12-31"])
    assert [s.items["free_cf"] for s in yahoo.parse_statements({"cashflow": partial}, "TRY")] == [None, None]


def test_currency_comes_from_yahoo_or_the_market_default_flagged(monkeypatch):
    assert yahoo.YahooFundamentals._currency("TR", {"financialCurrency": "USD"}) == ("USD", False)
    assert yahoo.YahooFundamentals._currency("TR", {"financialCurrency": " EUR "}) == ("EUR", False)
    assert yahoo.YahooFundamentals._currency("TR", {}) == ("TRY", True)
    assert yahoo.YahooFundamentals._currency("US", {"financialCurrency": ""}) == ("USD", True)
    assert yahoo.YahooFundamentals._currency("XX", {}) == (None, False)  # nothing to assume: not flagged, simply unknown
    assert yahoo.YahooFundamentals._currency("TR", {"financialCurrency": "USD", "currency": "TRY"}, "currency") == ("TRY", False)
    _fake_yahoo(monkeypatch, info={"marketCap": 1.0})
    yp = yahoo.YahooFundamentals()
    sts = yp.statements("TR", "ASELS", "annual")
    assert sts and all(s.currency == "TRY" and s.currency_assumed for s in sts)
    snap = yp.snapshot("TR", "ASELS")
    assert (snap.currency, snap.currency_assumed, snap.quote_currency, snap.quote_currency_assumed) == ("TRY", True, "TRY", True)


def test_snapshot_money_is_split_between_the_listing_and_the_reporting_currency(monkeypatch):
    """THYAO's shape: files in USD, trades in TRY. Its 386 bn market cap, 52-week range and EPS are TRY figures, its
    statements and TTM revenue USD ones — the snapshot says so instead of calling the market cap "$386 bn"."""
    thyao = {"financialCurrency": "USD", "currency": "TRY", "marketCap": 386_983_919_616.0, "fiftyTwoWeekHigh": 355.5, "trailingEps": -6.76,
             "totalRevenue": 26_350_999_552.0, "netIncomeToCommon": 2_684_999_936.0}
    _fake_yahoo(monkeypatch, info=thyao)
    yp = yahoo.YahooFundamentals()
    snap = yp.snapshot("TR", "THYAO")
    assert (snap.quote_currency, snap.quote_currency_assumed, snap.currency, snap.currency_assumed) == ("TRY", False, "USD", False)
    assert snap.market_cap == 386_983_919_616.0 and snap.week52_high == 355.5 and snap.revenue_ttm == 26_350_999_552.0
    assert all(st.currency == "USD" and not st.currency_assumed for st in yp.statements("TR", "THYAO", "annual"))
    # A US ADR the other way round: quoted in USD, reporting in TRY.
    _fake_yahoo(monkeypatch, info={"financialCurrency": "TRY", "currency": "USD", "marketCap": 4_291_944_704.0})
    provider.reset()
    adr = yahoo.YahooFundamentals().snapshot("US", "TKC")
    assert (adr.quote_currency, adr.currency) == ("USD", "TRY")
    # Only the listing currency missing: it alone is assumed and flagged.
    _fake_yahoo(monkeypatch, info={"financialCurrency": "USD", "marketCap": 1.0})
    provider.reset()
    half = yahoo.YahooFundamentals().snapshot("TR", "THYAO")
    assert (half.currency, half.currency_assumed, half.quote_currency, half.quote_currency_assumed) == ("USD", False, "TRY", True)


# --- snapshot parsing + adapter behaviour ---------------------------------------------------------


def test_snapshot_reads_only_descriptive_info_keys_and_scales_fractions():
    snap = yahoo.parse_snapshot(INFO, date(2026, 9, 17), "TRY")
    assert snap.as_of == date(2026, 9, 17) and snap.currency == "TRY"
    assert (snap.market_cap, snap.pe, snap.forward_pe, snap.price_to_book, snap.beta) == (5e9, 12.5, 10.0, 2.1, 1.1)
    assert (snap.profit_margin, snap.return_on_equity, snap.payout_ratio) == (15.0, 20.0, 30.0)  # fractions → percentages
    assert snap.dividend_yield == 2.1  # trailingAnnualDividendYield ×100 when Yahoo has the dividend history
    assert snap.shares_outstanding == 456_000_000 and snap.float_shares == 2e8 and snap.week52_high == 99.5
    assert snap.week52_low is None and snap.ev_to_ebitda is None and snap.short_percent_of_float is None
    assert yahoo.parse_snapshot({"recommendationKey": "buy", "targetMeanPrice": 1.0}, date(2026, 9, 17), "TRY") is None  # nothing descriptive: no snapshot
    assert yahoo.parse_snapshot({"marketCap": "n/a", "beta": True}, date(2026, 9, 17), "TRY") is None
    # GARAN's shape: no dividend history at Yahoo → trailing 0.0 next to an 18 % payout; dividendYield (a percentage) is the figure.
    garan = {"trailingAnnualDividendYield": 0.0, "trailingAnnualDividendRate": 0.0, "dividendYield": 4.12, "payoutRatio": 0.1837}
    assert yahoo.parse_snapshot(garan, date(2026, 9, 17), "TRY").dividend_yield == 4.12
    assert yahoo.parse_snapshot({"trailingAnnualDividendYield": 0.0, "marketCap": 1.0}, date(2026, 9, 17), "TRY").dividend_yield is None  # neither stated: null, never 0
    assert yahoo.parse_snapshot({"trailingAnnualDividendYield": 0.0119, "dividendYield": 1.11}, date(2026, 9, 17), "TRY").dividend_yield == 1.19  # history present: trailing wins
    # The contract's keys, and none of the analyst fields, are what the adapter reads.
    assert set(yahoo.INFO_KEYS) == set(SNAPSHOT_KEYS)
    assert not [k for k in yahoo.INFO_KEYS.values() if k.startswith(("recommendation", "target", "numberOfAnalyst"))]
    assert yahoo.FRACTION_KEYS <= set(SNAPSHOT_KEYS)


def test_yahoo_adapter_maps_symbols_shares_info_and_classifies_failures(monkeypatch):
    asked = _fake_yahoo(monkeypatch)
    info_calls = []
    real = yahoo.fetch_info
    monkeypatch.setattr(yahoo, "fetch_info", lambda t: (info_calls.append(t), real(t))[1])
    yp = yahoo.YahooFundamentals()
    sts = yp.statements("TR", "ASELS", "annual")
    assert asked == [("ASELS.IS", "annual")] and len(sts) == 9
    assert yp.statements("TR", "ASELS", "quarterly") and yp.snapshot("TR", "ASELS").market_cap == 5e9
    assert info_calls == ["ASELS.IS"]  # one `info` fetch serves both periods and the snapshot
    assert yp.statements("US", "AAPL", "annual")[0].currency == "TRY" and asked[-1] == ("AAPL", "annual")  # US symbols go as-is
    st = yp.status()
    assert st.connected and st.last_tick_at is not None and st.error is None
    # A CUSIP placeholder is never sent to Yahoo; an empty answer is "nothing", not an outage.
    assert yp.statements("US", "037833100", "annual") == [] and yp.snapshot("US", "037833100") is None
    monkeypatch.setattr(yahoo, "fetch_statement_frames", lambda t, p: {"income": pd.DataFrame(), "balance": None, "cashflow": pd.DataFrame()})
    assert yp.statements("TR", "THIN", "annual") == [] and yp.status().connected is True and yp.status().error is None
    monkeypatch.setattr(yahoo, "fetch_info", lambda t: {})
    assert yp.snapshot("TR", "NOPE") is None
    # Transport failures, the rate limit (yfinance's own error or an HTTP 429) and server errors are outages
    # (ProviderUnavailable, remembered); Yahoo's "no such data" answers — a 404, its missing-ticker errors, an empty
    # fundamentals answer — are "nothing"; a ticker's own error (odd JSON) propagates as-is.
    from curl_cffi.requests.exceptions import HTTPError as CurlHTTPError
    from requests.exceptions import HTTPError as RequestsHTTPError
    from yfinance.exceptions import YFException, YFRateLimitError, YFTickerMissingError

    def raising(exc):
        return lambda *a: (_ for _ in ()).throw(exc)

    class Response:
        def __init__(self, status_code):
            self.status_code = status_code

    monkeypatch.setattr(yahoo, "fetch_statement_frames", raising(OSError("yahoo down")))
    with pytest.raises(ProviderUnavailable):
        yp.statements("TR", "ASELS", "annual")
    assert yp.status().connected is False and "yahoo down" in yp.status().error
    for outage in (YFRateLimitError(), CurlHTTPError("429", response=Response(429)), RequestsHTTPError("503", response=Response(503))):
        monkeypatch.setattr(yahoo, "fetch_statement_frames", raising(outage))
        with pytest.raises(ProviderUnavailable):
            yp.statements("TR", "ASELS", "annual")
    for nothing in (YFTickerMissingError("XXX", "delisted"), YFException("Empty fundamentals-timeseries result"), CurlHTTPError("404", response=Response(404))):
        monkeypatch.setattr(yahoo, "fetch_statement_frames", raising(nothing))
        monkeypatch.setattr(yahoo, "fetch_info", raising(nothing))
        yp._info = None
        assert yp.statements("TR", "XXX", "annual") == [] and yp.snapshot("TR", "XXX") is None
    assert (yahoo._is_outage(CurlHTTPError("404", response=Response(404))), yahoo._is_nothing(YFRateLimitError()), yahoo._is_nothing(ValueError("x"))) == (False, False, False)
    monkeypatch.setattr(yahoo, "fetch_info", raising(ValueError("odd json")))
    with pytest.raises(ValueError):
        yp.snapshot("TR", "NEWCO")
    # The remembered `info` is the last ticker's, for a minute: another ticker or an older entry is refetched.
    monkeypatch.setattr(yahoo, "fetch_info", lambda t: {"financialCurrency": "USD", "marketCap": 7.0})
    assert yp.snapshot("TR", "NEWCO").market_cap == 7.0  # NEWCO's info is now the remembered one
    monkeypatch.setattr(yahoo, "fetch_info", lambda t: {"financialCurrency": "USD", "marketCap": 8.0})
    assert yp.snapshot("TR", "NEWCO").market_cap == 7.0  # still the cached answer
    ticker, at, info = yp._info
    yp._info = (ticker, at - timedelta(seconds=yahoo.INFO_TTL_S + 1), info)
    assert yp.snapshot("TR", "NEWCO").market_cap == 8.0


def test_yfinance_exception_hiding_is_switched_off_and_frames_are_fetched_one_by_one(monkeypatch):
    """With yfinance's default `hide_exceptions=True` a rate-limited statements fetch comes back as an empty frame
    — "nothing", and the run keeps hammering Yahoo. Every Yahoo call goes through `_yfinance()`, which turns it off."""
    from yfinance.config import YfConfig
    from yfinance.exceptions import YFException, YFRateLimitError

    YfConfig.debug.hide_exceptions = True
    yahoo._yfinance()
    assert YfConfig.debug.hide_exceptions is False

    class Ticker:
        """A ticker Yahoo has an annual income statement and balance sheet for, but no cash flow (a BIST shape)."""

        def __init__(self, symbol):
            self.symbol = symbol

        income_stmt = ANNUAL["income"]
        balance_sheet = ANNUAL["balance"]

        @property
        def cashflow(self):
            raise YFException("Empty fundamentals-timeseries result")

        @property
        def quarterly_income_stmt(self):
            raise YFRateLimitError()

    monkeypatch.setattr(yahoo, "_yfinance", lambda: type("yf", (), {"Ticker": Ticker}))
    frames = yahoo.fetch_statement_frames("ASELS.IS", "annual")
    assert frames["income"] is ANNUAL["income"] and frames["balance"] is ANNUAL["balance"] and frames["cashflow"] is None  # the missing one, not all three
    assert len(yahoo.parse_statements(frames, "TRY")) == 6
    with pytest.raises(YFRateLimitError):  # an outage propagates, for _guard to turn into ProviderUnavailable
        yahoo.fetch_statement_frames("ASELS.IS", "quarterly")


# --- refresh --------------------------------------------------------------------------------------


def _instruments(session, *symbols: str, market: str = "TR") -> list[Instrument]:
    """Instruments on a watchlist, so `universe()` (and a refresh without explicit symbols) picks them up."""
    resolver = EntityResolver(session)
    resolver.ensure_markets()
    out = [resolver.instrument(market, s) for s in symbols]  # type: ignore[arg-type]
    watchlist = Watchlist(owner_id="1", name="w")
    session.add(watchlist)
    session.flush()
    session.add_all([WatchlistItem(watchlist_id=watchlist.id, instrument_id=i.id) for i in out])
    session.flush()
    return out


def test_refresh_upserts_idempotently_and_isolates_failures(session, monkeypatch, caplog):
    asels, thyao, garan = _instruments(session, "ASELS", "THYAO", "GARAN")
    _fake_yahoo(monkeypatch)
    today = datetime.now(UTC).date()
    assert fundamentals.refresh(session, "TR", pause_s=0) == 3 * 20  # per instrument: 9 annual + 10 quarterly statements (no quarterly cash flow) + 1 snapshot
    rows = session.scalars(select(Fundamental).where(Fundamental.instrument_id == asels.id)).all()
    assert len(rows) == 19 and {r.source for r in rows} == {"yahoo"} and {r.currency for r in rows} == {"TRY"}
    assert all(r.fetched_at is not None for r in rows)
    newest = next(r for r in rows if r.kind == "income" and r.period_kind == "annual" and r.period_end == date(2025, 12, 31))
    assert newest.items["revenue"] == 1000.0 and newest.items["eps_diluted"] == 1.5
    snap = session.scalar(select(FundamentalSnapshot).where(FundamentalSnapshot.instrument_id == asels.id))
    assert snap.as_of == today and snap.metrics["market_cap"] == 5e9 and snap.metrics["dividend_yield"] == 2.1 and snap.metrics["week52_low"] is None
    assert set(snap.metrics) == set(SNAPSHOT_KEYS) and "recommendationKey" not in snap.metrics
    assert (asels.shares_outstanding, asels.shares_as_of) == (456_000_000, today)

    # A re-run replaces in place: same row count, new numbers, a fresher fetched_at.
    revised = {"annual": {**ANNUAL, "income": _frame({"Total Revenue": [1100.0, 800.0, 640.0], "Net Income": [160.0, 100.0, 80.0]}, ANNUAL_ENDS)}, "quarterly": QUARTERLY}
    _fake_yahoo(monkeypatch, frames=revised, info={**INFO, "marketCap": 6e9, "sharesOutstanding": 460_000_000})
    provider.reset()  # a new process (the CLI) — the adapter instance remembers the last ticker's `info` for a minute otherwise
    assert fundamentals.refresh(session, "TR", symbols=["asels"], pause_s=0) == 20
    rows = session.scalars(select(Fundamental).where(Fundamental.instrument_id == asels.id)).all()
    assert len(rows) == 19
    newest = next(r for r in rows if r.kind == "income" and r.period_kind == "annual" and r.period_end == date(2025, 12, 31))
    assert newest.items["revenue"] == 1100.0 and newest.items["gross_profit"] is None and newest.fetched_at >= snap.fetched_at
    snaps = session.scalars(select(FundamentalSnapshot).where(FundamentalSnapshot.instrument_id == asels.id)).all()
    assert len(snaps) == 1 and snaps[0].metrics["market_cap"] == 6e9 and asels.shares_outstanding == 460_000_000

    # One ticker's own failure is logged and skipped; the others are still written.
    def flaky(ticker, period):
        if ticker == "THYAO.IS":
            raise ValueError("bad json")
        return {"annual": ANNUAL, "quarterly": QUARTERLY}[period]

    monkeypatch.setattr(yahoo, "fetch_statement_frames", flaky)
    before = session.scalar(select(Fundamental.fetched_at).where(Fundamental.instrument_id == thyao.id).limit(1))
    with caplog.at_level("WARNING", logger="instilens.fundamentals"):
        assert fundamentals.refresh(session, "TR", pause_s=0) == 2 * 20
    assert any("THYAO skipped: ValueError: bad json" in r.getMessage() for r in caplog.records)
    assert session.scalar(select(Fundamental.fetched_at).where(Fundamental.instrument_id == thyao.id).limit(1)) == before
    assert session.scalar(select(Fundamental.fetched_at).where(Fundamental.instrument_id == garan.id).limit(1)) > before

    # An outage stops the batch cleanly: what was written before it stays, the rest is untouched, nothing raises.
    def outage(ticker, period):
        if ticker == "GARAN.IS":
            raise OSError("yahoo down")
        return {"annual": ANNUAL, "quarterly": QUARTERLY}[period]

    monkeypatch.setattr(yahoo, "fetch_statement_frames", outage)
    caplog.clear()
    with caplog.at_level("WARNING", logger="instilens.fundamentals"):
        assert fundamentals.refresh(session, "TR", pause_s=0) == 20  # ASELS only; GARAN (second in symbol order) tripped the outage
    assert any("yahoo unavailable, stopping after 20 rows" in r.getMessage() for r in caplog.records)
    assert provider.resolve_provider().status().connected is False
    # Unknown symbols are reported, not invented; a symbol Yahoo has nothing for writes nothing.
    monkeypatch.setattr(yahoo, "fetch_statement_frames", lambda t, p: {})
    monkeypatch.setattr(yahoo, "fetch_info", lambda t: {})
    provider.reset()
    caplog.clear()
    with caplog.at_level("INFO", logger="instilens.fundamentals"):
        assert fundamentals.refresh(session, "TR", symbols=["NOPE", "ASELS"], pause_s=0) == 0
    assert any("unknown symbols skipped: NOPE" in r.getMessage() for r in caplog.records)
    assert any("ASELS has nothing at yahoo" in r.getMessage() for r in caplog.records)


def test_refresh_flags_an_assumed_currency_and_paces_every_provider_call(session, monkeypatch, caplog):
    _instruments(session, "ASELS", "THYAO")
    asked = _fake_yahoo(monkeypatch, info={"marketCap": 1.0})
    naps = []
    monkeypatch.setattr(fundamentals.time, "sleep", naps.append)
    with caplog.at_level("WARNING", logger="instilens.fundamentals"):
        fundamentals.refresh(session, "TR", pause_s=0.25)
    assert naps == [0.25] * 5  # two instruments × (annual, quarterly, snapshot) = six provider calls: a nap before each but the first
    assert [r.getMessage() for r in caplog.records if "states no currency; TRY assumed" in r.getMessage()] == [
        "TR fundamentals: ASELS states no currency; TRY assumed", "TR fundamentals: THYAO states no currency; TRY assumed"]
    assert {r.currency for r in session.scalars(select(Fundamental))} == {"TRY"}
    assert {(r.currency, r.quote_currency) for r in session.scalars(select(FundamentalSnapshot))} == {("TRY", "TRY")}
    # A period Yahoo has no statements for ends that instrument's statement pulls: an ETF is one annual call, not two.
    monkeypatch.setattr(yahoo, "fetch_statement_frames", lambda t, p: (asked.append((t, p)), {})[1])
    asked.clear()
    provider.reset()
    fundamentals.refresh(session, "TR", symbols=["ASELS"], pause_s=0)
    assert asked == [("ASELS.IS", "annual")]


# --- derived ratios -------------------------------------------------------------------------------


def _st(kind, end, **items) -> Statement:
    return Statement(kind, date.fromisoformat(end), "TRY", dict.fromkeys(CANONICAL_KEYS[kind]) | items)


def test_derived_ratios_margins_leverage_and_yoy_alignment():
    annual = yahoo.parse_statements(ANNUAL, "TRY")
    assert fundamentals.derived(annual) == {"gross_margin": 40.0, "operating_margin": 20.0, "net_margin": 15.0, "fcf_margin": 20.0,
                                            "debt_to_equity": 50.0, "revenue_growth_yoy": 25.0, "net_income_growth_yoy": 50.0, "period_end": "2025-12-31"}
    quarterly = yahoo.parse_statements(QUARTERLY, "TRY")
    q = fundamentals.derived(quarterly)
    # Newest quarter (2025-06-30) against the same quarter a year earlier (2024-06-30), never the previous quarter.
    assert (q["revenue_growth_yoy"], q["net_income_growth_yoy"], q["period_end"]) == (50.0, 125.0, "2025-06-30")
    assert (q["gross_margin"], q["net_margin"], q["debt_to_equity"]) == (40.0, 15.0, 50.0) and q["fcf_margin"] is None and q["operating_margin"] is None
    # Fiscal-calendar drift is tolerated, a 9-months-earlier statement is not a year-earlier one.
    drift = [_st("income", "2025-06-28", revenue=200.0), _st("income", "2024-06-29", revenue=100.0)]
    assert fundamentals.derived(drift)["revenue_growth_yoy"] == 100.0
    assert fundamentals.derived([_st("income", "2025-06-30", revenue=200.0), _st("income", "2024-09-30", revenue=100.0)])["revenue_growth_yoy"] is None
    # Zero / missing denominators are None, a shrinking loss reads as positive growth.
    zero = [_st("income", "2025-12-31", revenue=0.0, gross_profit=5.0, net_income=-10.0), _st("income", "2024-12-31", revenue=0.0, net_income=-40.0),
            _st("balance", "2025-12-31", total_debt=10.0, equity=0.0)]
    d = fundamentals.derived(zero)
    assert d["gross_margin"] is None and d["revenue_growth_yoy"] is None and d["debt_to_equity"] is None and d["net_income_growth_yoy"] == 75.0
    assert fundamentals.derived([]) == dict.fromkeys((*fundamentals.DERIVED_KEYS, "period_end"))
    # Only a balance sheet: leverage from it, period_end from it, everything income-based None.
    only_balance = fundamentals.derived([_st("balance", "2025-12-31", total_debt=30.0, equity=120.0)])
    assert only_balance["debt_to_equity"] == 25.0 and only_balance["period_end"] == "2025-12-31" and only_balance["net_margin"] is None
    # The balance sheet of the income period is preferred; an older one is not used when a same-period one exists.
    mixed = [_st("income", "2025-12-31", revenue=100.0), _st("balance", "2025-12-31", total_debt=10.0, equity=100.0), _st("balance", "2024-12-31", total_debt=90.0, equity=100.0)]
    assert fundamentals.derived(mixed)["debt_to_equity"] == 10.0
    # ...but a cash flow statement of another period never feeds fcf_margin.
    assert fundamentals.derived([_st("income", "2025-12-31", revenue=100.0), _st("cashflow", "2024-12-31", free_cf=50.0)])["fcf_margin"] is None


# --- API + stock_detail ---------------------------------------------------------------------------


def _client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    token = auth.issue_token(auth.register(session, "f@example.com", "correct-horse-1", "F"))
    c = TestClient(app)
    c.headers["authorization"] = f"Bearer {token}"
    return c, app


def test_route_shapes_and_stock_detail_summary(session, pipeline_run, monkeypatch):
    c, app = _client(session)
    try:
        assert TestClient(app).get("/api/v1/stocks/ASELS/fundamentals").status_code == 401
        assert c.get("/api/v1/stocks/NOPE/fundamentals").status_code == 404
        assert c.get("/api/v1/stocks/ASELS/fundamentals", params={"period": "ttm"}).status_code == 422
        # Known symbol, nothing fetched yet: the empty state, never a placeholder number.
        empty = c.get("/api/v1/stocks/ASELS/fundamentals").json()
        assert empty == {"symbol": "ASELS", "name": empty["name"], "market": "TR", "currency": "TRY", "source": "yahoo", "fetched_at": None, "snapshot": None,
                         "period": "annual", "statements": {"income": [], "balance": [], "cashflow": []},
                         "derived": dict.fromkeys((*fundamentals.DERIVED_KEYS, "period_end"))}
        assert c.get("/api/v1/stocks/ASELS").json()["fundamentals"] is None

        _fake_yahoo(monkeypatch)
        assert fundamentals.refresh(session, "TR", symbols=["ASELS"], pause_s=0) == 20
        body = c.get("/api/v1/stocks/asels/fundamentals").json()
        assert set(body) == {"symbol", "name", "market", "currency", "source", "fetched_at", "snapshot", "period", "statements", "derived"}
        assert body["symbol"] == "ASELS" and body["currency"] == "TRY" and body["source"] == "yahoo" and body["period"] == "annual"
        assert datetime.fromisoformat(body["fetched_at"]) is not None
        snap = body["snapshot"]
        assert set(snap) == {"as_of", "quote_currency", *SNAPSHOT_KEYS} and snap["market_cap"] == 5e9 and snap["profit_margin"] == 15.0 and snap["ev_to_ebitda"] is None
        assert snap["quote_currency"] == "TRY"
        assert [s["period_end"] for s in body["statements"]["income"]] == ANNUAL_ENDS
        assert body["statements"]["income"][0]["items"]["revenue"] == 1000.0 and body["statements"]["income"][2]["items"]["operating_income"] is None
        assert body["statements"]["cashflow"][0]["items"]["free_cf"] == 200.0
        assert set(body["statements"]["balance"][0]["items"]) == set(CANONICAL_KEYS["balance"])
        assert body["derived"] == {"gross_margin": 40.0, "operating_margin": 20.0, "net_margin": 15.0, "fcf_margin": 20.0, "debt_to_equity": 50.0,
                                   "revenue_growth_yoy": 25.0, "net_income_growth_yoy": 50.0, "period_end": "2025-12-31"}
        q = c.get("/api/v1/stocks/ASELS/fundamentals", params={"period": "quarterly"}).json()
        assert q["period"] == "quarterly" and [s["period_end"] for s in q["statements"]["income"]] == QUARTER_ENDS and q["statements"]["cashflow"] == []
        assert q["snapshot"] == snap and q["derived"]["revenue_growth_yoy"] == 50.0 and q["derived"]["period_end"] == "2025-06-30"

        detail = c.get("/api/v1/stocks/ASELS").json()["fundamentals"]
        assert detail == {"as_of": snap["as_of"], "market_cap": 5e9, "pe": 12.5, "price_to_book": 2.1, "net_margin": 15.0, "revenue_growth_yoy": 25.0,
                          "dividend_yield": 2.1, "shares_outstanding": 456_000_000, "currency": "TRY", "quote_currency": "TRY", "source": "yahoo"}
        assert c.get("/api/v1/stocks/THYAO").json()["fundamentals"] is None  # not refreshed: still the empty state
        assert analytics.stock_detail(session, "TR", "ASELS")["fundamentals"] == detail

        # THYAO's shape (reports in USD, trades in TRY): the payload's currency is the reporting one, the snapshot names the listing one.
        _fake_yahoo(monkeypatch, info={**INFO, "financialCurrency": "USD", "currency": "TRY"})
        provider.reset()
        assert fundamentals.refresh(session, "TR", symbols=["THYAO"], pause_s=0) == 20
        body = c.get("/api/v1/stocks/THYAO/fundamentals").json()
        assert body["currency"] == "USD" and body["snapshot"]["quote_currency"] == "TRY" and body["snapshot"]["market_cap"] == 5e9
        summary = c.get("/api/v1/stocks/THYAO").json()["fundamentals"]
        assert (summary["currency"], summary["quote_currency"], summary["market_cap"]) == ("USD", "TRY", 5e9)
    finally:
        app.dependency_overrides.clear()


def test_payload_caps_statements_and_summary_without_a_snapshot(session, monkeypatch):
    (inst,) = _instruments(session, "ASELS")
    ends = [f"{2025 - i}-12-31" for i in range(10)]
    frames = {"annual": {"income": _frame({"Total Revenue": [float(100 + i) for i in range(10)]}, ends)}, "quarterly": {}}
    _fake_yahoo(monkeypatch, frames=frames, info={})  # no snapshot at all
    assert fundamentals.refresh(session, "TR", pause_s=0) == 10
    body = fundamentals.stock_fundamentals(session, "TR", "ASELS", "annual")
    assert len(body["statements"]["income"]) == fundamentals.MAX_STATEMENTS == 8 and body["snapshot"] is None and body["fetched_at"] is not None
    assert body["derived"]["revenue_growth_yoy"] == round((100 - 101) / 101 * 100, 2)
    assert fundamentals.summary(session, inst.id) == {"as_of": "2025-12-31", "market_cap": None, "pe": None, "price_to_book": None, "net_margin": None,
                                                      "revenue_growth_yoy": -0.99, "dividend_yield": None, "shares_outstanding": None, "currency": "TRY",
                                                      "quote_currency": None, "source": "yahoo"}
    assert inst.shares_outstanding is None and fundamentals.summary(session, 999_999) is None


# --- AI tools -------------------------------------------------------------------------------------


def test_tools_return_compact_json_with_provenance(session, monkeypatch):
    import json

    from anthropic import beta_tool

    from instilens.ai.prompts import SYSTEM_PROMPT
    from instilens.ai.tools import build_tools

    _instruments(session, "ASELS")
    _fake_yahoo(monkeypatch)
    fundamentals.refresh(session, "TR", pause_s=0)
    tools = {fn.__name__: beta_tool(fn) for fn in build_tools(session)}
    for name in ("get_income_statements", "get_balance_sheets", "get_cash_flow_statements", "get_financial_metrics"):
        desc = tools[name].to_dict()["description"]
        assert "currency" in desc and "source" in desc and "null" in desc and "recommendation" in desc and "verdict" not in desc.split("never")[0]
    schema = tools["get_income_statements"].to_dict()["input_schema"]
    assert schema["properties"]["period"]["enum"] == ["annual", "quarterly"] and schema["required"] == ["symbol"]

    inc = json.loads(tools["get_income_statements"].call({"symbol": "asels", "limit": 2}))
    assert set(inc) == {"symbol", "name", "currency", "source", "fetched_at", "period", "statements"}
    assert inc["currency"] == "TRY" and inc["source"] == "yahoo" and inc["period"] == "annual" and len(inc["statements"]) == 2
    assert inc["statements"][0] == {"period_end": "2025-12-31", "items": {"revenue": 1000.0, "cost_of_revenue": 600.0, "gross_profit": 400.0, "operating_income": 200.0,
                                                                          "ebitda": 250.0, "pretax_income": 190.0, "net_income": 150.0, "eps_diluted": 1.5, "interest_expense": 10.0}}
    assert len(json.loads(tools["get_income_statements"].call({"symbol": "ASELS", "limit": 50}))["statements"]) == 3  # capped by what exists (≤ 8)
    bal = json.loads(tools["get_balance_sheets"].call({"symbol": "ASELS", "period": "quarterly"}))
    assert bal["period"] == "quarterly" and len(bal["statements"]) == 4 and bal["statements"][0]["items"]["equity"] == 820.0
    cf = json.loads(tools["get_cash_flow_statements"].call({"symbol": "ASELS"}))
    assert cf["statements"][0]["items"]["free_cf"] == 200.0 and cf["statements"][0]["items"]["share_repurchase"] is None
    assert json.loads(tools["get_cash_flow_statements"].call({"symbol": "ASELS", "period": "quarterly"}))["statements"] == []
    met = json.loads(tools["get_financial_metrics"].call({"symbol": "ASELS"}))
    assert set(met) == {"symbol", "name", "currency", "source", "fetched_at", "snapshot", "derived"}
    assert met["snapshot"]["pe"] == 12.5 and met["snapshot"]["quote_currency"] == "TRY" and met["derived"]["net_margin"] == 15.0 and "recommendationKey" not in json.dumps(met)
    metrics_desc = tools["get_financial_metrics"].to_dict()["description"]
    assert "quote_currency" in metrics_desc and "estimate" in metrics_desc  # both currencies named; forward P/E declared as estimate-based
    for name in ("get_income_statements", "get_balance_sheets", "get_cash_flow_statements", "get_financial_metrics"):
        assert "error" in json.loads(tools[name].call({"symbol": "NOPE"}))
    assert "get_financial_metrics" in SYSTEM_PROMPT and "period_end" in SYSTEM_PROMPT


# --- migration ------------------------------------------------------------------------------------


def test_migration_creates_fundamentals_tables_on_scratch_sqlite(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from instilens.config import BACKEND_ROOT

    url = f"sqlite:///{tmp_path / 'scratch.db'}"
    monkeypatch.setattr(settings, "database_url", url)  # migrations/env.py reads the URL from settings
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    engine = create_engine(url)
    insp = inspect(engine)
    assert {"fundamentals", "fundamental_snapshots"} <= set(insp.get_table_names())
    assert {c["name"] for c in insp.get_columns("fundamentals")} == {"id", "instrument_id", "kind", "period_kind", "period_end", "currency", "items", "source", "fetched_at"}
    assert {c["name"] for c in insp.get_columns("fundamental_snapshots")} == {"id", "instrument_id", "as_of", "metrics", "currency", "quote_currency", "source", "fetched_at"}
    assert {"shares_outstanding", "shares_as_of"} <= {c["name"] for c in insp.get_columns("instruments")}
    uniques = [set(u["column_names"]) for u in insp.get_unique_constraints("fundamentals")]
    assert {"instrument_id", "kind", "period_kind", "period_end"} in uniques
    assert {"instrument_id", "as_of"} in [set(u["column_names"]) for u in insp.get_unique_constraints("fundamental_snapshots")]
    with engine.begin() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "a3b4c5d6e7f8"  # the current head (plans / portfolios / billing builds on searchable_texts)
        conn.execute(text("INSERT INTO fundamentals (instrument_id, kind, period_kind, period_end, currency, items, source, fetched_at) "
                          "VALUES (1, 'income', 'annual', '2025-12-31', 'TRY', '{\"revenue\": 1.0}', 'yahoo', '2026-09-17 10:00:00')"))
        with pytest.raises(Exception):  # noqa: B017 — the unique key, whatever the driver calls the violation
            conn.execute(text("INSERT INTO fundamentals (instrument_id, kind, period_kind, period_end, currency, items, source, fetched_at) "
                              "VALUES (1, 'income', 'annual', '2025-12-31', 'TRY', '{}', 'yahoo', '2026-09-17 10:00:00')"))
    engine.dispose()
    command.downgrade(cfg, "c9d0e1f2a3b4")
    engine = create_engine(url)
    insp = inspect(engine)
    assert {"fundamentals", "fundamental_snapshots"}.isdisjoint(insp.get_table_names())
    assert {"shares_outstanding", "shares_as_of"}.isdisjoint(c["name"] for c in insp.get_columns("instruments"))
    engine.dispose()


# --- universe -------------------------------------------------------------------------------------


def test_universe_is_flows_and_watchlists_without_placeholders(session):
    resolver = EntityResolver(session)
    resolver.ensure_markets()
    asels, stale, idle, traded = (resolver.instrument(Market.TR, s) for s in ("ASELS", "STALE", "IDLE", "TRADED"))
    aapl, placeholder = resolver.instrument(Market.US, "AAPL"), resolver.instrument(Market.US, "CUSIP:037833100")
    assert placeholder.symbol == "037833100"
    institution = resolver.institution(Market.TR, "MOID-X", "X Portföy")
    fund = resolver.fund("XFN", institution)
    today = date.today()
    snap = PortfolioSnapshot(fund_id=fund.id, as_of=today, source="KAP")
    session.add(snap)
    session.flush()

    def change(inst, days_ago):
        return PositionChange(fund_id=fund.id, instrument_id=inst.id, to_snapshot_id=snap.id, period_end=today - timedelta(days=days_ago),
                              from_qty=0, to_qty=10, delta_qty=10, activity="NEW")

    session.add_all([change(asels, 10), change(stale, fundamentals.UNIVERSE_DAYS + 1), change(placeholder, 10)])
    disclosure = Disclosure(market_code="TR", source="KAP", source_id="1", kind="KAP_SHARE_TRANSACTION", published_at=datetime.now(UTC), raw_hash="x", payload={})
    session.add(disclosure)
    session.flush()
    session.add(TransactionEvent(disclosure_id=disclosure.id, market_code="TR", instrument_id=traded.id, institution_id=institution.id, side="BUY", buy_nominal=1,
                                 sell_nominal=0, net_nominal=1, effective_date=today - timedelta(days=5), published_at=datetime.now(UTC), confidence="EXACT"))
    watchlist = Watchlist(owner_id="1", name="w")
    session.add(watchlist)
    session.flush()
    session.add_all([WatchlistItem(watchlist_id=watchlist.id, instrument_id=aapl.id), WatchlistItem(watchlist_id=watchlist.id, fund_id=fund.id)])
    session.flush()
    assert [i.symbol for i in fundamentals.universe(session, "TR")] == ["ASELS", "TRADED"]  # STALE too old, IDLE seen nowhere
    assert [i.symbol for i in fundamentals.universe(session, "US")] == ["AAPL"]  # the CUSIP placeholder is never asked for
    assert idle.symbol == "IDLE"
    # Stalest first: a name never fetched leads, then the oldest fetched_at over statements and snapshots; the cap
    # takes the head of that order, so a weekly job reaches the tail of the alphabet too.
    zzzz = resolver.instrument(Market.TR, "ZZZZ")
    session.add_all([change(idle, 3), change(zzzz, 3)])
    session.flush()
    now = datetime.now(UTC)
    session.add_all([
        FundamentalSnapshot(instrument_id=asels.id, as_of=today, metrics={}, source="yahoo", fetched_at=now - timedelta(days=1)),
        Fundamental(instrument_id=asels.id, kind="income", period_kind="annual", period_end=today, items={}, source="yahoo", fetched_at=now - timedelta(days=30)),
        FundamentalSnapshot(instrument_id=traded.id, as_of=today, metrics={}, source="yahoo", fetched_at=now - timedelta(days=8)),
        FundamentalSnapshot(instrument_id=idle.id, as_of=today, metrics={}, source="yahoo", fetched_at=now - timedelta(days=15)),
    ])
    session.flush()
    assert [i.symbol for i in fundamentals.universe(session, "TR")] == ["ZZZZ", "IDLE", "TRADED", "ASELS"]  # ASELS: its newest row is a day old
    assert [i.symbol for i in fundamentals.universe(session, "TR", limit=2)] == ["ZZZZ", "IDLE"]


def test_scheduler_job_is_gated_by_the_runtime_setting(monkeypatch):
    from instilens import scheduler

    calls = []
    monkeypatch.setattr(fundamentals, "refresh", lambda s, market: calls.append(market) or 0)
    monkeypatch.setattr(scheduler, "session_scope", lambda: __import__("contextlib").nullcontext(object()))
    monkeypatch.setattr(settings, "fundamentals_enabled", False)
    scheduler.fundamentals()
    assert calls == []
    monkeypatch.setattr(settings, "fundamentals_enabled", True)
    scheduler.fundamentals()
    assert calls == ["TR", "US"]
    from instilens.services import runtime_settings

    assert runtime_settings.EDITABLE["fundamentals_enabled"] == {"type": "bool", "group": "data"}
    assert runtime_settings.coerce("fundamentals_enabled", "off") is False
    assert runtime_settings.EDITABLE["fundamentals_max_instruments"] == {"type": "int", "group": "data", "min": 10, "max": 5000}
    assert runtime_settings.coerce("fundamentals_max_instruments", "250") == 250


def test_refresh_is_capped_by_the_runtime_setting_unless_symbols_are_named(session, monkeypatch):
    _instruments(session, "ASELS", "GARAN", "THYAO")
    _fake_yahoo(monkeypatch)
    monkeypatch.setattr(settings, "fundamentals_max_instruments", 2)
    assert fundamentals.refresh(session, "TR", pause_s=0) == 2 * 20
    fetched = {i.symbol for i in session.scalars(select(Instrument)) if session.scalar(select(FundamentalSnapshot).where(FundamentalSnapshot.instrument_id == i.id))}
    assert fetched == {"ASELS", "GARAN"}  # none fetched yet: symbol order breaks the tie
    provider.reset()
    assert fundamentals.refresh(session, "TR", pause_s=0) == 2 * 20  # the next run: THYAO (never fetched) first, then the stalest of the two
    assert session.scalar(select(FundamentalSnapshot).where(FundamentalSnapshot.instrument_id == session.scalar(select(Instrument.id).where(Instrument.symbol == "THYAO")))) is not None
    provider.reset()
    assert fundamentals.refresh(session, "TR", symbols=["ASELS", "GARAN", "THYAO"], pause_s=0) == 3 * 20  # named symbols: uncapped
