"""Chart candles: the provider's `intraday_bars` behind a monkeypatched yfinance frame, the Matriks slot's refusal,
the candles service (daily from the table or the provider, intraday cache + last-good, per-key fetch locks) and the route."""

import threading
import time
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from instilens.config import settings
from instilens.domain.models import MarketPrice
from instilens.ingestion.prices import matriks, provider, yahoo
from instilens.ingestion.prices.provider import Candle, ProviderUnavailable
from instilens.services import auth, candles, quotes, runtime_settings
from instilens.services.entities import EntityResolver

IST = ZoneInfo("Europe/Istanbul")
NY = ZoneInfo("America/New_York")
T0 = datetime(2026, 9, 16, 9, 30, tzinfo=UTC)  # Wednesday 12:30 Istanbul: BIST open


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Provider instances, the candle/quote caches and the price settings must not leak between tests."""
    runtime_settings.defaults()  # capture .env defaults before any test patches `settings`
    monkeypatch.setattr(settings, "price_provider", "yahoo")
    for key in ("matriks_api_key", "matriks_base_url", "matriks_ws_url"):
        monkeypatch.setattr(settings, key, None)
    provider.reset()
    quotes.reset()
    candles.reset()
    yield
    provider.reset()
    quotes.reset()
    candles.reset()


def _intraday(rows: list[tuple], start: datetime, freq: str = "5min") -> pd.DataFrame:
    """A `Ticker.history(interval=...)` frame: flat OHLCV columns over a tz-aware DatetimeIndex in the exchange's zone."""
    idx = pd.date_range(start=start, periods=len(rows), freq=freq, name="Datetime")
    return pd.DataFrame({f: [r[i] for r in rows] for i, f in enumerate(("Open", "High", "Low", "Close", "Volume"))}, index=idx)


def _history(bars: dict[str, list[tuple]], end: date) -> pd.DataFrame:
    """A yf.download(group_by='ticker') history frame: columns (ticker, field), one row per day ending `end`."""
    n = max(len(v) for v in bars.values())
    idx = pd.date_range(end=end, periods=n, freq="D")
    cols, data = [], {}
    for tk, rows in bars.items():
        for i, field in enumerate(("Open", "High", "Low", "Close", "Volume")):
            cols.append((tk, field))
            data[(tk, field)] = [r[i] for r in rows]
    return pd.DataFrame(data, index=idx, columns=pd.MultiIndex.from_tuples(cols))


ROWS = [(100.0, 101.0, 99.5, 100.5, 1000), (100.5, 102.0, 100.0, 101.5, 2000), (101.5, 101.5, 100.0, 100.2, 500)]


# --- Yahoo adapter ---------------------------------------------------------------------------


def test_intraday_window_stays_inside_yahoos_limits():
    # 5m: 78 bars a session → 300 bars need four sessions; 15m/1000 would need 61 days, one more than Yahoo keeps —
    # capped two days inside (midnight start + a server date a day behind the exchange's).
    assert yahoo.intraday_window_days("5m", 300) == 13
    assert yahoo.intraday_window_days("15m", 1000) == 58 and yahoo.intraday_window_days("5m", 1000) == 25
    assert yahoo.intraday_window_days("1h", 1000) == 223 and yahoo.intraday_window_days("1h", 100_000) == 728
    with pytest.raises(ValueError):
        yahoo.intraday_window_days("1d", 10)


def test_parse_candles_converts_to_utc_and_drops_holes():
    frame = _intraday(ROWS + [(None, 103.0, 100.0, 101.0, 10), (float("nan"), float("nan"), float("nan"), float("nan"), float("nan")), (101.0, 101.0, 101.0, 101.0, None)],
                      datetime(2026, 9, 16, 10, 0, tzinfo=IST))
    out = yahoo.parse_candles(frame, "ASELS")
    assert [c.at for c in out] == [datetime(2026, 9, 16, 7, m, tzinfo=UTC) for m in (0, 5, 10)] + [datetime(2026, 9, 16, 7, 25, tzinfo=UTC)]
    assert out[0] == Candle("ASELS", datetime(2026, 9, 16, 7, 0, tzinfo=UTC), Decimal("100"), Decimal("101"), Decimal("99.5"), Decimal("100.5"), 1000)
    assert all(isinstance(c.open, Decimal) for c in out) and out[1].volume == 2000
    assert out[-1].volume is None and out[-1].close == Decimal("101")  # a missing volume stays None, the bar stays
    # A New York index converts too; a naive one (a daily frame, a fixture) is read as UTC; a scrambled index is sorted.
    ny = yahoo.parse_candles(_intraday(ROWS[:1], datetime(2026, 9, 16, 9, 30, tzinfo=NY)), "AAPL")
    assert ny[0].at == datetime(2026, 9, 16, 13, 30, tzinfo=UTC)
    naive = yahoo.parse_candles(_intraday(ROWS[:1], datetime(2026, 9, 16, 9, 30)), "AAPL")
    assert naive[0].at == datetime(2026, 9, 16, 9, 30, tzinfo=UTC)
    shuffled = _intraday(ROWS, datetime(2026, 9, 16, 10, 0, tzinfo=IST)).iloc[::-1]
    assert [c.close for c in yahoo.parse_candles(shuffled, "ASELS")] == [Decimal("100.5"), Decimal("101.5"), Decimal("100.2")]
    assert yahoo.parse_candles(None, "ASELS") == [] and yahoo.parse_candles(pd.DataFrame(), "ASELS") == []


def test_yahoo_intraday_bars_map_the_symbol_trim_to_lookback_and_report_outages(monkeypatch):
    asked = []

    def fake(ticker, interval, lookback):
        asked.append((ticker, interval, lookback))
        return _intraday(ROWS, datetime(2026, 9, 16, 10, 0, tzinfo=IST))

    monkeypatch.setattr(yahoo, "fetch_intraday", fake)
    yp = yahoo.YahooProvider()
    out = yp.intraday_bars("TR", "ASELS", "5m", 2)
    assert asked == [("ASELS.IS", "5m", 2)]
    assert [c.symbol for c in out] == ["ASELS", "ASELS"] and [c.close for c in out] == [Decimal("101.5"), Decimal("100.2")]  # the newest two
    st = yp.status()
    assert st.connected is True and st.error is None and st.last_tick_at is not None and st.delay == "delayed"
    assert yp.intraday_bars("US", "AAPL", "1h", 10)[0].symbol == "AAPL" and asked[-1] == ("AAPL", "1h", 10)
    # A CUSIP placeholder is never sent to Yahoo, and says nothing about the connection.
    assert yp.intraday_bars("US", "037833100", "5m", 10) == [] and len(asked) == 2
    # A symbol Yahoo answers "nothing" for (delisted, unknown there) is no bars, not an outage: the connection verdict stands.
    from yfinance.exceptions import YFPricesMissingError, YFTzMissingError

    for missing in (YFPricesMissingError("OLD.IS", " (5m 2026-09-03 -> 2026-09-16)"), YFTzMissingError("OLD.IS")):
        monkeypatch.setattr(yahoo, "fetch_intraday", lambda *a, exc=missing: (_ for _ in ()).throw(exc))
        assert yp.intraday_bars("TR", "OLD", "5m", 10) == []
    assert yp.status().connected is True and yp.status().error is None

    # An exception and an empty frame (yfinance's shape for a swallowed network error) are both an outage.
    monkeypatch.setattr(yahoo, "fetch_intraday", lambda *a: (_ for _ in ()).throw(OSError("yahoo down")))
    with pytest.raises(ProviderUnavailable):
        yp.intraday_bars("TR", "ASELS", "5m", 10)
    st = yp.status()
    assert st.connected is False and st.error == "intraday: yahoo down" and st.last_tick_at is not None
    monkeypatch.setattr(yahoo, "fetch_intraday", lambda *a: pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"], index=pd.DatetimeIndex([])))
    with pytest.raises(ProviderUnavailable) as exc:
        yp.intraday_bars("TR", "ASELS", "15m", 10)
    assert "no 15m bars for ASELS.IS" in str(exc.value) and yp.status().error == "intraday: no 15m bars for ASELS.IS"


def test_matriks_slot_refuses_intraday(monkeypatch):
    slot = matriks.MatriksProvider()
    with pytest.raises(provider.ProviderNotConfigured):
        slot.intraday_bars("TR", "ASELS", "5m", 10)
    for key in ("matriks_api_key", "matriks_base_url", "matriks_ws_url"):
        monkeypatch.setattr(settings, key, "set")
    with pytest.raises(ProviderUnavailable) as exc:
        slot.intraday_bars("TR", "ASELS", "5m", 10)
    assert str(exc.value) == matriks.REFUSAL


# --- service: daily ---------------------------------------------------------------------------


def _instrument(session, market="TR", symbol="ASELS", name="Aselsan"):
    resolver = EntityResolver(session)
    resolver.ensure_markets()
    inst = resolver.instrument(market, symbol, name)
    session.flush()
    return inst


def _rows(session, inst, days: list[tuple[date, tuple]], source="yahoo"):
    for d, (o, h, lo, c, v) in days:
        session.add(MarketPrice(instrument_id=inst.id, trade_date=d, open=o, high=h, low=lo, close=c, volume=v, source=source))
    session.flush()


def test_daily_bars_come_from_the_table_when_it_holds_full_ohlc(session, monkeypatch):
    inst = _instrument(session)
    _rows(session, inst, [(date(2026, 9, 14), (Decimal("100"), Decimal("104"), Decimal("99"), Decimal("103"), 1_000)),
                          (date(2026, 9, 15), (Decimal("103"), Decimal("106"), Decimal("102"), Decimal("105"), None)),
                          (date(2026, 9, 16), (Decimal("105"), Decimal("105"), Decimal("101"), Decimal("102"), 900))])
    monkeypatch.setattr(yahoo, "fetch_history", lambda *a: (_ for _ in ()).throw(AssertionError("the table answers, Yahoo is not asked")))
    out = candles.candles(session, "TR", "asels", "1d", now=T0)
    # Table rows are last night's close whoever printed them: `delay` says "eod", not the provider's 15 minutes.
    assert {k: v for k, v in out.items() if k != "bars"} == {"symbol": "ASELS", "name": "Aselsan", "market": "TR", "currency": "TRY", "interval": "1d",
                                                             "source": "yahoo", "delay": "eod", "delayed": True, "as_of": T0.isoformat(), "tz": "Europe/Istanbul"}
    assert out["bars"] == [
        {"t": 1789344000, "o": 100.0, "h": 104.0, "l": 99.0, "c": 103.0, "v": 1000},  # 2026-09-14 00:00 UTC
        {"t": 1789430400, "o": 103.0, "h": 106.0, "l": 102.0, "c": 105.0, "v": None},
        {"t": 1789516800, "o": 105.0, "h": 105.0, "l": 101.0, "c": 102.0, "v": 900},
    ]
    assert datetime.fromtimestamp(out["bars"][0]["t"], UTC) == datetime(2026, 9, 14, tzinfo=UTC)
    # `lookback` keeps the newest bars; the source is the newest row's.
    two = candles.candles(session, "TR", "ASELS", "1d", lookback=2, now=T0)["bars"]
    assert [b["c"] for b in two] == [105.0, 102.0]
    assert candles.candles(session, "TR", "THYAO", "1d", now=T0) is None  # unknown symbol → the route's 404


def test_daily_falls_back_to_the_provider_when_a_row_lacks_ohlc_or_is_not_a_providers(session, monkeypatch):
    """A close-only legacy row (the CSV loader) anywhere in the range: the provider answers the whole range — the
    chart never draws a candle padded from a close. Yahoo's own holes are dropped, the rest trimmed to `lookback`."""
    inst = _instrument(session)
    _rows(session, inst, [(date(2026, 9, 14), (Decimal("100"), Decimal("104"), Decimal("99"), Decimal("103"), 1_000))])
    _rows(session, inst, [(date(2026, 9, 15), (None, None, None, Decimal("105"), None))], source="csv")
    asked = []

    def fake(tickers, start):
        asked.append((list(tickers), start))
        return _history({"ASELS.IS": [(90.0, 91.0, 89.0, 90.5, 10), (None, 92.0, 90.0, 91.0, 20), (91.0, 93.0, 90.5, 92.5, 30), (92.5, 94.0, 92.0, 93.0, 40)]}, end=date(2026, 9, 16))

    monkeypatch.setattr(yahoo, "fetch_history", fake)
    out = candles.candles(session, "TR", "ASELS", "1d", lookback=2, now=T0)
    assert asked == [(["ASELS.IS"], date.today() - timedelta(days=2 * 7 // 5 + 14))]
    # The provider's daily answer carries its own delay (today's partial session is in it), not the table's "eod".
    assert out["source"] == "yahoo" and out["delay"] == "delayed" and out["delayed"] is True and out["as_of"] == T0.isoformat()
    assert out["bars"] == [{"t": 1789430400, "o": 91.0, "h": 93.0, "l": 90.5, "c": 92.5, "v": 30}, {"t": 1789516800, "o": 92.5, "h": 94.0, "l": 92.0, "c": 93.0, "v": 40}]
    # Ten bars asked: the hole is dropped and three remain — never a padded fourth.
    assert [b["c"] for b in candles.candles(session, "TR", "ASELS", "1d", lookback=10, now=T0 + timedelta(seconds=61))["bars"]] == [90.5, 92.5, 93.0]
    # An empty table is the same fallback.
    session.execute(MarketPrice.__table__.delete())
    assert len(candles.candles(session, "TR", "ASELS", "1d", lookback=10, now=T0 + timedelta(seconds=122))["bars"]) == 3
    # A CSV close laid over a provider row keeps that row's open/high/low but is CSV data: the provider answers, so the
    # widget never labels a CSV number "Yahoo Finance".
    _rows(session, inst, [(date(2026, 9, 14), (Decimal("100"), Decimal("104"), Decimal("99"), Decimal("103"), 1_000))])
    _rows(session, inst, [(date(2026, 9, 15), (Decimal("103"), Decimal("106"), Decimal("102"), Decimal("105"), None))], source="csv")
    assert len(candles.candles(session, "TR", "ASELS", "1d", lookback=10, now=T0 + timedelta(seconds=183))["bars"]) == 3 and len(asked) == 4
    with pytest.raises(ValueError):
        candles.candles(session, "TR", "ASELS", "2m", now=T0)


# --- service: intraday cache + last-good -----------------------------------------------------


def test_intraday_is_cached_60s_per_key_and_the_last_good_answer_outlives_an_outage(session, monkeypatch, caplog):
    _instrument(session)
    _instrument(session, "US", "AAPL", "Apple")
    calls = []

    def fake(ticker, interval, lookback):
        calls.append((ticker, interval, lookback))
        start = datetime(2026, 9, 16, 10, 0, tzinfo=IST) if ticker.endswith(".IS") else datetime(2026, 9, 16, 9, 30, tzinfo=NY)
        return _intraday(ROWS, start, freq="5min" if interval == "5m" else "15min")

    monkeypatch.setattr(yahoo, "fetch_intraday", fake)
    out = candles.candles(session, "TR", "ASELS", "5m", lookback=10, now=T0)
    assert calls == [("ASELS.IS", "5m", 10)]
    assert {k: v for k, v in out.items() if k != "bars"} == {"symbol": "ASELS", "name": "Aselsan", "market": "TR", "currency": "TRY", "interval": "5m",
                                                             "source": "yahoo", "delay": "delayed", "delayed": True, "as_of": T0.isoformat(), "tz": "Europe/Istanbul"}
    assert out["bars"][0] == {"t": int(datetime(2026, 9, 16, 10, 0, tzinfo=IST).timestamp()), "o": 100.0, "h": 101.0, "l": 99.5, "c": 100.5, "v": 1000}
    assert [b["t"] for b in out["bars"]] == sorted(b["t"] for b in out["bars"]) and len(out["bars"]) == 3

    # Within 60 s the cache answers — also for a smaller lookback (a slice), and per (market, symbol, interval).
    again = candles.candles(session, "TR", "ASELS", "5m", lookback=2, now=T0 + timedelta(seconds=59))
    assert len(calls) == 1 and [b["c"] for b in again["bars"]] == [101.5, 100.2] and again["as_of"] == T0.isoformat()
    candles.candles(session, "TR", "ASELS", "15m", lookback=10, now=T0 + timedelta(seconds=10))
    us = candles.candles(session, "US", "AAPL", "5m", lookback=10, now=T0 + timedelta(seconds=10))
    assert calls[1:] == [("ASELS.IS", "15m", 10), ("AAPL", "5m", 10)]
    assert us["currency"] == "USD" and us["tz"] == "America/New_York" and us["bars"][0]["t"] == int(datetime(2026, 9, 16, 9, 30, tzinfo=NY).timestamp())
    # A larger lookback than the cached answer holds is a miss; 60 s after a fetch it is refreshed.
    candles.candles(session, "TR", "ASELS", "5m", lookback=20, now=T0 + timedelta(seconds=20))
    assert calls[-1] == ("ASELS.IS", "5m", 20)
    candles.candles(session, "TR", "ASELS", "5m", lookback=10, now=T0 + timedelta(seconds=79))
    assert len(calls) == 4
    t1 = T0 + timedelta(seconds=80)
    candles.candles(session, "TR", "ASELS", "5m", lookback=10, now=t1)
    assert len(calls) == 5

    # Yahoo down: the remembered answer is served under ITS fetch time, flagged `stale` (a fresh answer carries no such
    # key), logged; a key never answered propagates.
    monkeypatch.setattr(yahoo, "fetch_intraday", lambda *a: (_ for _ in ()).throw(OSError("yahoo down")))
    with caplog.at_level("WARNING", logger="instilens.services.candles"):
        stale = candles.candles(session, "TR", "ASELS", "5m", lookback=10, now=t1 + timedelta(seconds=200))
    assert stale["as_of"] == t1.isoformat() and [b["c"] for b in stale["bars"]] == [100.5, 101.5, 100.2] and stale["stale"] is True and "stale" not in out
    assert any("yahoo TR ASELS 5m failed, serving the answer of " + t1.isoformat() in r.getMessage() and "yahoo down" in r.getMessage() for r in caplog.records)
    with pytest.raises(ProviderUnavailable):
        candles.candles(session, "TR", "ASELS", "1h", lookback=10, now=t1)
    assert provider.resolve_provider().status().connected is False


def _gated_fetch(calls: list[str], gates: dict[str, threading.Event]):
    """A `fetch_intraday` that records the ticker and, for a gated one, waits until its gate is set."""

    def fake(ticker, interval, lookback):
        calls.append(ticker)
        gate = gates.get(ticker)
        if gate is not None:
            assert gate.wait(5), f"{ticker} was never released"
        start = datetime(2026, 9, 16, 10, 0, tzinfo=IST) if ticker.endswith(".IS") else datetime(2026, 9, 16, 9, 30, tzinfo=NY)
        return _intraday(ROWS, start)

    return fake


def _wait_for(cond, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not cond() and time.monotonic() < deadline:
        time.sleep(0.005)
    assert cond()


def test_a_slow_fetch_holds_only_its_own_key_and_concurrent_callers_of_one_key_share_a_fetch(monkeypatch):
    """The Yahoo round-trip runs under a per-key lock, never the cache lock: a hung AAPL fetch does not delay ASELS
    (nor a cache hit), while two callers asking for ASELS at once cost one fetch."""
    calls: list[str] = []
    gates = {"AAPL": threading.Event()}
    monkeypatch.setattr(yahoo, "fetch_intraday", _gated_fetch(calls, gates))
    yp = provider.resolve_provider()
    aapl = threading.Thread(target=candles._from_provider, args=(yp, "US", "AAPL", "5m", 10, T0), daemon=True)
    aapl.start()
    _wait_for(lambda: calls == ["AAPL"])
    # AAPL hangs inside Yahoo; ASELS is fetched and answered meanwhile, and a second ASELS call is a cache hit.
    out = candles._from_provider(yp, "TR", "ASELS", "5m", 10, T0)
    assert [b["c"] for b in out["bars"]] == [100.5, 101.5, 100.2] and calls == ["AAPL", "ASELS.IS"] and aapl.is_alive()
    assert candles._from_provider(yp, "TR", "ASELS", "5m", 5, T0 + timedelta(seconds=30))["bars"][-1]["c"] == 100.2 and len(calls) == 2
    gates["AAPL"].set()
    aapl.join(5)
    assert not aapl.is_alive() and candles._cache[("US", "AAPL", "5m")]["lookback"] == 10

    # Two callers of one uncached key at once: the second queues behind the first's fetch and takes its answer.
    calls.clear()
    gates["ASELS.IS"] = threading.Event()
    results: list[dict] = []
    threads = [threading.Thread(target=lambda: results.append(candles._from_provider(yp, "TR", "ASELS", "15m", 10, T0)), daemon=True) for _ in range(2)]
    for th in threads:
        th.start()
    _wait_for(lambda: calls == ["ASELS.IS"])
    time.sleep(0.05)  # the second caller has had time to reach the per-key lock
    gates["ASELS.IS"].set()
    for th in threads:
        th.join(5)
    assert calls == ["ASELS.IS"] and len(results) == 2 and results[0]["bars"] == results[1]["bars"]


def test_cache_keeps_the_most_recently_served_keys_only(monkeypatch):
    monkeypatch.setattr(candles, "CACHE_KEYS", 2)
    monkeypatch.setattr(yahoo, "fetch_intraday", _gated_fetch([], {}))
    yp = provider.resolve_provider()
    for symbol in ("ASELS", "THYAO"):
        candles._from_provider(yp, "TR", symbol, "5m", 10, T0)
    candles._from_provider(yp, "TR", "ASELS", "5m", 10, T0 + timedelta(seconds=10))  # a hit: ASELS is the recent one again
    candles._from_provider(yp, "TR", "EREGL", "5m", 10, T0 + timedelta(seconds=20))
    assert [k[1] for k in candles._cache] == ["ASELS", "EREGL"] and set(candles._fetching) == {("TR", "ASELS", "5m"), ("TR", "EREGL", "5m")}


# --- route ------------------------------------------------------------------------------------


def _client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    c = TestClient(app)
    token = auth.issue_token(auth.register(session, "chart@example.com", "password123", "C"))
    return c, {"authorization": f"Bearer {token}"}


def test_candles_route_validates_and_maps_provider_errors(session, monkeypatch):
    from instilens.api.main import app

    _instrument(session)
    monkeypatch.setattr(yahoo, "fetch_intraday", lambda ticker, interval, lookback: _intraday(ROWS, datetime(2026, 9, 16, 10, 0, tzinfo=IST)))
    c, h = _client(session)
    try:
        assert c.get("/api/v1/stocks/ASELS/candles").status_code == 401
        assert c.get("/api/v1/stocks/THYAO/candles", headers=h).status_code == 404
        assert c.get("/api/v1/stocks/ASELS/candles?interval=2m", headers=h).status_code == 422
        assert c.get("/api/v1/stocks/ASELS/candles?lookback=9", headers=h).status_code == 422
        assert c.get("/api/v1/stocks/ASELS/candles?lookback=1001", headers=h).status_code == 422
        assert c.get("/api/v1/stocks/ASELS/candles?market=DE", headers=h).status_code == 422

        r = c.get("/api/v1/stocks/asels/candles?interval=5m&lookback=10", headers=h)
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"symbol", "name", "market", "currency", "interval", "source", "delay", "delayed", "as_of", "tz", "bars"}
        assert body["symbol"] == "ASELS" and body["interval"] == "5m" and body["source"] == "yahoo" and body["delay"] == "delayed" and body["delayed"] is True and body["tz"] == "Europe/Istanbul"
        assert datetime.fromisoformat(body["as_of"]).tzinfo is not None
        assert [set(b) for b in body["bars"]] == [{"t", "o", "h", "l", "c", "v"}] * 3 and [b["c"] for b in body["bars"]] == [100.5, 101.5, 100.2]
        # The default: daily, 300 bars — an empty table sends the provider for the range.
        monkeypatch.setattr(yahoo, "fetch_history", lambda tickers, start: _history({"ASELS.IS": [(90.0, 91.0, 89.0, 90.5, 10)]}, end=date(2026, 9, 16)))
        body = c.get("/api/v1/stocks/ASELS/candles", headers=h).json()
        assert body["interval"] == "1d" and body["delay"] == "delayed" and body["bars"] == [{"t": 1789516800, "o": 90.0, "h": 91.0, "l": 89.0, "c": 90.5, "v": 10}]

        # An outage with nothing remembered for the key is 503; the Matriks slot, once selected, is the same 503.
        monkeypatch.setattr(yahoo, "fetch_intraday", lambda *a: (_ for _ in ()).throw(OSError("yahoo down")))
        r = c.get("/api/v1/stocks/ASELS/candles?interval=1h", headers=h)
        assert r.status_code == 503 and r.json() == {"detail": "provider unavailable"}
        assert "stale" not in c.get("/api/v1/stocks/ASELS/candles?interval=5m&lookback=10", headers=h).json()  # still a cache hit
        monkeypatch.setattr(candles, "CACHE_SECONDS", 0)  # expired: the remembered answer is served through the outage, flagged
        r = c.get("/api/v1/stocks/ASELS/candles?interval=5m&lookback=10", headers=h)
        assert r.status_code == 200 and r.json()["stale"] is True
        runtime_settings.set_many(session, {"price_provider": "matriks"}, "chart@example.com")
        for key in ("matriks_api_key", "matriks_base_url", "matriks_ws_url"):
            monkeypatch.setattr(settings, key, "set")
        r = c.get("/api/v1/stocks/ASELS/candles?interval=15m", headers=h)
        assert r.status_code == 503 and r.json() == {"detail": "provider unavailable"}
        assert c.get("/api/v1/stocks/ASELS/candles?interval=5m&lookback=10", headers=h).json()["source"] == "yahoo"  # its remembered answer still serves
    finally:
        runtime_settings.set_many(session, {"price_provider": "yahoo"}, "chart@example.com")
        app.dependency_overrides.clear()
    assert session.scalar(select(MarketPrice)) is None  # the chart never writes the table
