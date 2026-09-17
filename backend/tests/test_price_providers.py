"""Price providers (Faz 2a): registry + fallback, the Yahoo adapter behind monkeypatched frames, the Matriks slot,
the OHLCV loader, the quote feed loop with its heartbeat, and the admin surface."""

from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from instilens.config import settings
from instilens.domain.models import LiveEvent, MarketPrice
from instilens.ingestion.prices import load_prices, matriks, provider, yahoo
from instilens.ingestion.prices.provider import (
    Bar,
    ProviderNotConfigured,
    ProviderStatus,
    ProviderUnavailable,
    Tick,
)
from instilens.services import auth, feed, quotes, runtime_settings

IST = ZoneInfo("Europe/Istanbul")
OPEN_TR = datetime(2026, 9, 16, 12, 0, tzinfo=IST)  # Wednesday noon Istanbul: BIST open, NYSE pre-market


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    """Provider instances, the quote cache and the price settings must not leak between tests."""
    runtime_settings.defaults()  # capture .env defaults before any test patches `settings`
    monkeypatch.setattr(settings, "price_provider", "yahoo")
    monkeypatch.setattr(settings, "quotes_interval_s", 60)
    for key in ("matriks_api_key", "matriks_base_url", "matriks_ws_url"):
        monkeypatch.setattr(settings, key, None)
    provider.reset()
    quotes.reset()
    yield
    provider.reset()
    quotes.reset()


class FakeProvider:
    """A configured provider whose prints the test controls; `fail=True` makes it an outage."""

    def __init__(self, name: str = "fake", delay: str = "realtime"):
        self.name, self.delay = name, delay
        self.prices: dict[str, tuple[float, float | None]] = {"USDTRY=X": (41.41, 1.0), "XU100.IS": (10150.4, 1.5)}
        self.calls, self.fail = 0, False

    def quotes(self, tickers):
        self.calls += 1
        if self.fail:
            raise ProviderUnavailable(f"{self.name} down")
        return {t: Tick(t, p, c, date(2026, 9, 16), None) for t, (p, c) in self.prices.items() if t in tickers}

    def daily_bars(self, market, symbols, start):
        return []

    def status(self):
        return ProviderStatus(self.name, configured=True, connected=not self.fail, delay=self.delay, last_tick_at=None, error=f"{self.name} down" if self.fail else None, note=None)


# --- registry + fallback ---------------------------------------------------------------------


def test_registry_builds_each_provider_and_rejects_unknown():
    assert isinstance(provider.build_provider("yahoo"), yahoo.YahooProvider)
    assert isinstance(provider.build_provider("matriks"), matriks.MatriksProvider)
    with pytest.raises(ValueError):
        provider.build_provider("bloomberg")
    assert provider.PROVIDERS == ("yahoo", "matriks")


def test_unconfigured_matriks_falls_back_to_yahoo_and_logs_once(caplog, monkeypatch):
    monkeypatch.setattr(settings, "price_provider", "matriks")
    with caplog.at_level("WARNING", logger="instilens.prices"):
        first = provider.resolve_provider()
        second = provider.resolve_provider()
    assert isinstance(first, yahoo.YahooProvider) and second is first  # one instance, remembered
    fallbacks = [r for r in caplog.records if "not configured" in r.getMessage()]
    assert len(fallbacks) == 1 and "matriks" in fallbacks[0].getMessage() and "yahoo" in fallbacks[0].getMessage()

    # Once the three keys are present the choice is honoured — the slot is "configured" even though it cannot answer yet.
    for key in ("matriks_api_key", "matriks_base_url", "matriks_ws_url"):
        monkeypatch.setattr(settings, key, "set")
    assert isinstance(provider.resolve_provider(), matriks.MatriksProvider)

    # An unknown name is a configuration error: logged once, Yahoo answers.
    monkeypatch.setattr(settings, "price_provider", "bloomberg")
    with caplog.at_level("WARNING", logger="instilens.prices"):
        assert isinstance(provider.resolve_provider(), yahoo.YahooProvider)
        provider.resolve_provider()
    assert len([r for r in caplog.records if "unknown price provider" in r.getMessage()]) == 1


def test_matriks_slot_reports_honestly_and_refuses_data(monkeypatch):
    slot = matriks.MatriksProvider()
    st = slot.status()
    # "delayed" until the vendor confirms the entitlement: the one delay claim that cannot overstate it.
    assert st.name == "matriks" and st.configured is False and st.connected is False and st.delay == "delayed"
    assert st.last_tick_at is None and st.error is None and "awaits Matriks API documentation" in st.note
    assert st.as_dict()["last_tick_at"] is None and st.as_dict()["note"] == matriks.NOTE
    with pytest.raises(ProviderNotConfigured):
        slot.quotes(["XU100"])
    with pytest.raises(ProviderUnavailable):
        slot.daily_bars("TR", ["ASELS"], date(2026, 9, 1))
    for key in ("matriks_api_key", "matriks_base_url", "matriks_ws_url"):
        monkeypatch.setattr(settings, key, "set")
    st = slot.status()
    assert st.configured is True and st.connected is False and st.delay == "delayed"
    assert st.error == matriks.REFUSAL and "docs/08-price-providers.md" in st.error  # the refusal is named, not inferred from the note
    with pytest.raises(ProviderUnavailable) as exc:  # configured but no adapter: still unavailable, never a guessed request
        slot.quotes(["XU100"])
    assert str(exc.value) == st.error
    # Nothing in the module dials anything: no URL in the source, no network module imported.
    import inspect as _inspect

    assert "://" not in _inspect.getsource(matriks)
    assert not {"httpx", "requests", "socket", "websockets", "paho"} & set(vars(matriks))


# --- Yahoo adapter behind monkeypatched frames ---------------------------------------------


def _outage_frame(tickers) -> pd.DataFrame:
    """What yf.download hands back when every ticker failed (network/HTTP errors are swallowed and only logged):
    the usual (ticker, field) columns over an empty DatetimeIndex — `frame.empty` is True, nothing parses."""
    cols = pd.MultiIndex.from_product([list(tickers), ["Open", "High", "Low", "Close", "Volume"]])
    return pd.DataFrame(index=pd.DatetimeIndex([]), columns=cols, dtype="float64")


def _history(bars: dict[str, list[tuple]], end: date | None = None) -> pd.DataFrame:
    """A yf.download(group_by='ticker') history frame: columns (ticker, field), one row per day ending `end`."""
    n = max(len(v) for v in bars.values())
    idx = pd.date_range(end=end or date.today(), periods=n, freq="D")
    cols, data = [], {}
    for tk, rows in bars.items():
        padded = [(None,) * 5] * (n - len(rows)) + list(rows)
        for i, field in enumerate(("Open", "High", "Low", "Close", "Volume")):
            cols.append((tk, field))
            data[(tk, field)] = [r[i] for r in padded]
    return pd.DataFrame(data, index=idx, columns=pd.MultiIndex.from_tuples(cols))


def test_yahoo_daily_bars_are_ohlcv_keyed_by_instrument_symbol(monkeypatch):
    asked = []

    def fake(tickers, start):
        asked.append((list(tickers), start))
        return _history({"ASELS.IS": [(100.0, 104.5, 99.1, 103.25, 1_200_000), (103.0, None, 102.0, 105.0, None), (None, None, None, None, None)],
                         "THYAO.IS": [(300.0, 301.0, 299.0, 300.5, 10)]})

    monkeypatch.setattr(yahoo, "fetch_history", fake)
    yp = yahoo.YahooProvider()
    assert yp.status().connected is False and yp.status().last_tick_at is None
    bars = yp.daily_bars("TR", ["ASELS", "THYAO", "GARAN"], date(2026, 9, 1))
    assert asked == [(["ASELS.IS", "THYAO.IS", "GARAN.IS"], date(2026, 9, 1))]
    by = {}
    for b in bars:
        by.setdefault(b.symbol, []).append(b)
    assert set(by) == {"ASELS", "THYAO"}  # GARAN missing from the frame → no bars, no guess
    first = by["ASELS"][0]
    assert first == Bar("ASELS", first.trade_date, Decimal("100"), Decimal("104.5"), Decimal("99.1"), Decimal("103.25"), 1_200_000)
    assert isinstance(first.close, Decimal) and isinstance(first.volume, int)
    second = by["ASELS"][1]
    assert second.high is None and second.volume is None and second.close == Decimal("105")  # holes stay None
    assert len(by["ASELS"]) == 2  # the all-NaN row is dropped
    assert by["THYAO"][0].volume == 10
    st = yp.status()
    assert st.configured and st.connected and st.delay == "delayed" and st.last_tick_at is not None and st.error is None and st.note == yahoo.NOTE

    # US CUSIP placeholders are never sent to Yahoo; a flat single-ticker frame still parses.
    asked.clear()
    flat = pd.DataFrame({"Open": [10.0], "High": [11.0], "Low": [9.0], "Close": [10.5], "Volume": [5]}, index=pd.date_range(end=date.today(), periods=1, freq="D"))
    monkeypatch.setattr(yahoo, "fetch_history", lambda tickers, start: flat)
    bars = yp.daily_bars("US", ["037833100", "AAPL"], date(2026, 9, 1))
    assert [b.symbol for b in bars] == ["AAPL"] and bars[0].close == Decimal("10.5")

    # An outage is ProviderUnavailable and shows in the status, the earlier success stays remembered.
    monkeypatch.setattr(yahoo, "fetch_history", lambda tickers, start: (_ for _ in ()).throw(OSError("yahoo down")))
    with pytest.raises(ProviderUnavailable):
        yp.daily_bars("TR", ["ASELS"], date(2026, 9, 1))
    st = yp.status()
    assert st.connected is False and "yahoo down" in st.error and st.last_tick_at is not None

    # yfinance never raises on a network failure: it logs and returns a frame over an empty index. That is an outage
    # too — never "connected, no error, 0 bars".
    monkeypatch.setattr(yahoo, "fetch_history", lambda tickers, start: _outage_frame(tickers))
    yp2 = yahoo.YahooProvider()
    with pytest.raises(ProviderUnavailable) as exc:
        yp2.daily_bars("TR", ["ASELS", "THYAO"], date(2026, 9, 1))
    assert "no bars for any of 2 tickers" in str(exc.value)
    st = yp2.status()
    assert st.connected is False and st.error == "history: no bars for any of 2 tickers" and st.last_tick_at is None
    # Only unmappable symbols: Yahoo was never asked, so nothing is claimed either way.
    assert yp2.daily_bars("US", ["037833100"], date(2026, 9, 1)) == [] and yp2.status().error == st.error
    # One empty batch among answered ones is noted, not an outage.
    frames = iter([_outage_frame(["A.IS"]), _history({"B.IS": [(1.0, 1.0, 1.0, 1.0, 1)]})])
    monkeypatch.setattr(yahoo, "fetch_history", lambda tickers, start: next(frames))
    monkeypatch.setattr(yahoo, "BATCH", 1)
    assert [b.symbol for b in yp2.daily_bars("TR", ["A", "B"], date(2026, 9, 1))] == ["B"]
    st = yp2.status()
    assert st.connected is True and st.error == "history: 1 of 2 batches came back empty" and st.last_tick_at is not None


def test_yahoo_quotes_are_ticks_per_requested_ticker(monkeypatch):
    frame = _history({"USDTRY=X": [(41.0, 41.5, 40.9, 41.0, None), (41.4, 41.6, 41.2, 41.41, None)], "^GSPC": [(6500.0, 6510.0, 6400.0, 6435.0, 0)]}, end=date(2026, 9, 11))
    monkeypatch.setattr(yahoo, "fetch_frame", lambda tickers: frame)
    yp = yahoo.YahooProvider()
    ticks = yp.quotes(["USDTRY=X", "^GSPC", "XU100.IS"])
    assert set(ticks) == {"USDTRY=X", "^GSPC"}
    assert ticks["USDTRY=X"] == Tick("USDTRY=X", 41.41, 1.0, date(2026, 9, 11), None)
    assert ticks["^GSPC"].change_pct is None  # single bar
    assert yp.status().connected and yp.status().last_tick_at is not None
    monkeypatch.setattr(yahoo, "fetch_frame", lambda tickers: None)
    with pytest.raises(ProviderUnavailable):
        yp.quotes(["USDTRY=X"])
    assert yp.status().connected is False

    # A frame with nothing in it for any ticker (yfinance swallowed the network error) is the same outage.
    monkeypatch.setattr(yahoo, "fetch_frame", _outage_frame)
    with pytest.raises(ProviderUnavailable) as exc:
        yp.quotes(["USDTRY=X", "^GSPC"])
    assert "no data for any of 2 tickers" in str(exc.value)
    st = yp.status()
    assert st.connected is False and st.error == "quotes: no data for any of 2 tickers" and st.last_tick_at is not None
    # services/quotes keeps the last good numbers, flagged stale, through it.
    monkeypatch.setattr(provider, "build_provider", lambda name: yp)
    monkeypatch.setattr(yahoo, "fetch_frame", lambda tickers: frame)
    quotes.snapshot(now=OPEN_TR.astimezone(UTC))
    monkeypatch.setattr(yahoo, "fetch_frame", _outage_frame)
    stale = quotes.snapshot(now=OPEN_TR.astimezone(UTC), force=True)["quotes"]
    assert [q["key"] for q in stale] == ["USDTRY", "SPX"] and all(q["stale"] is True for q in stale)


def test_load_prices_writes_ohlcv_and_source(session, monkeypatch, caplog):
    from instilens.services.entities import EntityResolver

    resolver = EntityResolver(session)
    resolver.ensure_markets()
    asels, thyao = resolver.instrument("TR", "ASELS"), resolver.instrument("TR", "THYAO")
    session.flush()
    frame = _history({"ASELS.IS": [(100.0, 104.5, 99.1, 103.25, 1_200_000), (103.0, 106.0, 102.0, 105.0, 900_000)], "THYAO.IS": [(300.0, 301.0, 299.0, 300.5, 10)]})
    monkeypatch.setattr(yahoo, "fetch_history", lambda tickers, start: frame)
    assert load_prices(session, "TR", days=10) == 3
    rows = session.scalars(select(MarketPrice).where(MarketPrice.instrument_id == asels.id).order_by(MarketPrice.trade_date)).all()
    assert [(r.open, r.high, r.low, r.close, r.volume, r.source) for r in rows] == [
        (Decimal("100"), Decimal("104.5"), Decimal("99.1"), Decimal("103.25"), 1_200_000, "yahoo"),
        (Decimal("103"), Decimal("106"), Decimal("102"), Decimal("105"), 900_000, "yahoo"),
    ]
    assert session.scalar(select(MarketPrice.close).where(MarketPrice.instrument_id == thyao.id)) == Decimal("300.5")

    # A re-run updates in place (same keys, new close), never duplicates; a NaN hole in Open/High/Low/Volume on the
    # re-run (Yahoo leaves them in thin names) keeps yesterday's good value instead of erasing it.
    frame2 = _history({"ASELS.IS": [(100.0, 104.5, 99.1, 103.25, 1_200_000), (None, None, 102.5, 107.0, None)]})
    monkeypatch.setattr(yahoo, "fetch_history", lambda tickers, start: frame2)
    assert load_prices(session, "TR", days=10, symbols=["asels"]) == 2
    rows = session.scalars(select(MarketPrice).where(MarketPrice.instrument_id == asels.id).order_by(MarketPrice.trade_date)).all()
    assert len(rows) == 2 and (rows[1].open, rows[1].high, rows[1].low, rows[1].close, rows[1].volume) == (Decimal("103"), Decimal("106"), Decimal("102.5"), Decimal("107"), 900_000)

    # A configured-but-empty Matriks slot (or any outage) is logged, not raised: the pipeline around it goes on.
    for key in ("price_provider", "matriks_api_key", "matriks_base_url", "matriks_ws_url"):
        monkeypatch.setattr(settings, key, "matriks" if key == "price_provider" else "set")
    with caplog.at_level("WARNING", logger="instilens.prices"):
        assert load_prices(session, "TR", days=10) == 0
    assert any("matriks unavailable" in r.getMessage() for r in caplog.records)
    assert session.scalar(select(MarketPrice.close).where(MarketPrice.instrument_id == thyao.id)) == Decimal("300.5")  # untouched


# --- quotes service: source/delayed and provider switch ------------------------------------


def test_quotes_cache_invalidates_when_the_provider_changes(monkeypatch):
    fakes = {"yahoo": FakeProvider("yahoo", delay="delayed"), "matriks": FakeProvider("matriks")}
    monkeypatch.setattr(provider, "build_provider", lambda name: fakes[name])
    t0 = OPEN_TR.astimezone(UTC)
    out = quotes.snapshot(now=t0)
    assert [(q["key"], q["source"], q["delayed"]) for q in out["quotes"]] == [("USDTRY", "yahoo", True), ("XU100", "yahoo", True)]
    quotes.snapshot(now=t0 + timedelta(seconds=5))
    assert fakes["yahoo"].calls == 1  # cached
    monkeypatch.setattr(settings, "price_provider", "matriks")  # admin switched the runtime setting
    out = quotes.snapshot(now=t0 + timedelta(seconds=6))
    assert fakes["matriks"].calls == 1 and fakes["yahoo"].calls == 1
    assert all(q["source"] == "matriks" and q["delayed"] is False for q in out["quotes"])
    # force bypasses the cache (the feed process), a plain call does not.
    quotes.snapshot(now=t0 + timedelta(seconds=7))
    assert fakes["matriks"].calls == 1
    quotes.snapshot(now=t0 + timedelta(seconds=8), force=True)
    assert fakes["matriks"].calls == 2


# --- feed loop ------------------------------------------------------------------------------


@pytest.fixture
def feed_env(session, monkeypatch):
    """The feed's session_scope bound to the test database, and a controllable provider behind resolve_provider."""

    @contextmanager
    def scope():
        yield session
        session.flush()

    monkeypatch.setattr(feed, "session_scope", scope)
    fake = FakeProvider("fake")
    monkeypatch.setattr(provider, "build_provider", lambda name: fake)
    return fake


def _events(session):
    return session.scalars(select(LiveEvent).where(LiveEvent.kind == "quotes").order_by(LiveEvent.id)).all()


def test_feed_publishes_only_changes_and_writes_heartbeat(session, feed_env):
    fake = feed_env
    state = feed.FeedState()
    t0 = OPEN_TR.astimezone(UTC)
    hb = feed.run_once(state, now=t0)
    assert hb == {"running": True, "last_run_at": t0.isoformat(), "interval_s": 60, "published": 1, "provider": "fake", "provider_status": fake.status().as_dict(), "error": None}
    assert state.pruned_at == t0  # the first iteration also sweeps old live events
    evs = _events(session)
    assert len(evs) == 1 and evs[0].market_code is None
    payload = evs[0].payload
    assert set(payload) == {"as_of", "quotes", "markets"} and payload["as_of"] == t0.isoformat()
    assert [(q["key"], q["price"], q["source"]) for q in payload["quotes"]] == [("USDTRY", 41.41, "fake"), ("XU100", 10150, "fake")]
    assert runtime_settings.load_json(session, feed.HEARTBEAT_KEY) == hb

    # Identical prints a minute later: heartbeat moves, nothing is published.
    hb = feed.run_once(state, now=t0 + timedelta(seconds=60))
    assert hb["published"] == 1 and hb["last_run_at"] == (t0 + timedelta(seconds=60)).isoformat() and len(_events(session)) == 1
    assert fake.calls == 2  # the cache was bypassed both times
    assert runtime_settings.load_json(session, feed.HEARTBEAT_KEY)["last_run_at"] == hb["last_run_at"]

    # A moved price publishes once more.
    fake.prices["XU100.IS"] = (10200.0, 2.0)
    hb = feed.run_once(state, now=t0 + timedelta(seconds=120))
    assert hb["published"] == 2 and len(_events(session)) == 2 and _events(session)[-1].payload["quotes"][1]["price"] == 10200

    # Outage: the numbers come back flagged stale (that flag change is published), the error lands in the heartbeat, the loop lives on.
    fake.fail = True
    hb = feed.run_once(state, now=t0 + timedelta(seconds=180))
    assert hb["error"] == "fake down" and hb["published"] == 3 and hb["running"] is True
    assert hb["provider_status"]["connected"] is False and hb["provider_status"]["error"] == "fake down"  # the feed's own view, for /admin/providers
    assert all(q["stale"] is True for q in _events(session)[-1].payload["quotes"])
    hb = feed.run_once(state, now=t0 + timedelta(seconds=240))  # still down, still stale: nothing new
    assert hb["published"] == 3 and len(_events(session)) == 3

    # Even a broken publish path is survived and reported.
    fake.fail = False
    fake.prices["XU100.IS"] = (10300.0, 3.0)
    real = feed.live.publish
    feed.live.publish = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone"))
    try:
        hb = feed.run_once(state, now=t0 + timedelta(seconds=300))
    finally:
        feed.live.publish = real
    assert hb["error"] == "RuntimeError: db gone" and hb["published"] == 3
    hb = feed.run_once(state, now=t0 + timedelta(seconds=360))  # retried on the next tick
    assert hb["error"] is None and hb["published"] == 4

    # status(): alive while the last run is younger than 3 × interval, dead after.
    alive = feed.status(session, now=t0 + timedelta(seconds=360 + 179))
    assert alive["running"] is True and alive["provider_status"] == fake.status().as_dict()
    dead = feed.status(session, now=t0 + timedelta(seconds=360 + 181))
    assert dead["running"] is False and dead["published"] == 4 and dead["provider"] == "fake" and dead["interval_s"] == 60

    # The daily sweep: not before 24 h have passed, then once, and a failing sweep is retried next tick.
    assert state.pruned_at == t0
    feed.run_once(state, now=t0 + timedelta(hours=23))
    assert state.pruned_at == t0
    real_prune = feed.live.prune
    feed.live.prune = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("db gone"))
    try:
        hb = feed.run_once(state, now=t0 + timedelta(hours=25))
    finally:
        feed.live.prune = real_prune
    assert hb["error"] is None and state.pruned_at == t0  # a sweep miss is logged, never the heartbeat's error
    feed.run_once(state, now=t0 + timedelta(hours=25, seconds=60))
    assert state.pruned_at == t0 + timedelta(hours=25, seconds=60)


def test_feed_does_not_publish_an_empty_first_payload(session, feed_env):
    """Provider down at feed start: a fresh process remembers nothing, and `quotes: []` would blank every open strip
    (the tabs still hold the API's last-good numbers). Nothing is published until a real payload exists."""
    fake = feed_env
    fake.fail = True
    state = feed.FeedState()
    t0 = OPEN_TR.astimezone(UTC)
    hb = feed.run_once(state, now=t0)
    assert hb["published"] == 0 and hb["error"] == "fake down" and hb["provider_status"]["connected"] is False
    assert _events(session) == [] and state.last is not None  # seen, deliberately not published
    hb = feed.run_once(state, now=t0 + timedelta(seconds=60))
    assert hb["published"] == 0 and _events(session) == []
    fake.fail = False
    hb = feed.run_once(state, now=t0 + timedelta(seconds=120))
    assert hb["published"] == 1 and hb["error"] is None and len(_events(session)) == 1
    assert [q["key"] for q in _events(session)[0].payload["quotes"]] == ["USDTRY", "XU100"]


def test_feed_run_forever_stops_and_says_goodbye(session, feed_env):
    import threading

    class StopAfterFirstSleep(threading.Event):
        def wait(self, timeout=None):
            self.set()
            return True

    feed.run_forever(StopAfterFirstSleep())  # one iteration, one sleep, out
    hb = runtime_settings.load_json(session, feed.HEARTBEAT_KEY)
    assert hb["running"] is False and hb["published"] == 1 and len(_events(session)) == 1
    assert feed.status(session)["running"] is False


def test_feed_interval_follows_market_hours(monkeypatch):
    monkeypatch.setattr(settings, "quotes_interval_s", 30)
    assert feed.interval_s(OPEN_TR) == 30  # BIST open
    assert feed.interval_s(datetime(2026, 9, 16, 23, 30, tzinfo=IST)) == 30  # 16:30 New York: NYSE post-market
    assert feed.interval_s(datetime(2026, 9, 19, 12, 0, tzinfo=IST)) == feed.IDLE_INTERVAL_S  # Saturday
    assert feed.interval_s(datetime(2026, 9, 16, 9, 59, 30, tzinfo=IST)) == 31  # 30 s before the BIST open: wake up for it
    assert feed.interval_s(datetime(2026, 9, 16, 9, 59, 59, 800000, tzinfo=IST)) == 1


# --- admin surface ----------------------------------------------------------------------------


def _admin_client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    user = auth.register(session, "root@example.com", "correct-horse-1", "Root")
    user.role = "ADMIN"
    session.flush()
    return TestClient(app), {"authorization": f"Bearer {auth.issue_token(user)}"}


def test_admin_providers_shape_and_feed_liveness(session):
    from instilens.api.main import app

    c, h = _admin_client(session)
    try:
        assert c.get("/api/v1/admin/providers").status_code == 401
        body = c.get("/api/v1/admin/providers", headers=h).json()
        assert body["price"]["active"] == "yahoo" and body["price"]["selected"] == "yahoo" and body["price"]["available"] == ["yahoo", "matriks"]
        st = body["price"]["status"]
        assert st == {"name": "yahoo", "configured": True, "connected": False, "delay": "delayed", "last_tick_at": None, "error": None, "note": yahoo.NOTE}
        assert body["price"]["status_from"] == "api"  # no feed heartbeat: this worker's own instance
        assert body["feed"] == {"running": False, "last_run_at": None, "interval_s": 60, "published": 0, "provider": None, "error": None}

        # An old heartbeat is not "running", a fresh one is.
        old = datetime.now(UTC) - timedelta(minutes=10)
        runtime_settings.store_json(session, feed.HEARTBEAT_KEY, {"running": True, "last_run_at": old.isoformat(), "interval_s": 60, "published": 7, "provider": "yahoo", "error": None}, "feed")
        f = c.get("/api/v1/admin/providers", headers=h).json()["feed"]
        assert f["running"] is False and f["published"] == 7 and f["last_run_at"] == old.isoformat()
        runtime_settings.store_json(session, feed.HEARTBEAT_KEY, {"running": True, "last_run_at": datetime.now(UTC).isoformat(), "interval_s": 60, "published": 8, "provider": "yahoo", "error": "x"}, "feed")
        body = c.get("/api/v1/admin/providers", headers=h).json()
        f = body["feed"]
        assert f["running"] is True and f["published"] == 8 and f["error"] == "x"
        assert body["price"]["status_from"] == "api" and "provider_status" not in f  # a heartbeat without the provider's view (older feed)

        # While the feed runs, the card shows the provider as *that* process saw it — it drives the strip; this
        # worker's instance never fetched (connected=False above) and would contradict a healthy feed.
        seen = {"name": "yahoo", "configured": True, "connected": True, "delay": "delayed", "last_tick_at": "2026-09-16T09:00:00+00:00", "error": None, "note": yahoo.NOTE}
        runtime_settings.store_json(session, feed.HEARTBEAT_KEY, {"running": True, "last_run_at": datetime.now(UTC).isoformat(), "interval_s": 60, "published": 9, "provider": "yahoo", "provider_status": seen, "error": None}, "feed")
        body = c.get("/api/v1/admin/providers", headers=h).json()
        assert body["price"]["status"] == seen and body["price"]["status_from"] == "feed" and "provider_status" not in body["feed"]
        # ...unless the feed reports a different provider than the one this worker resolves (a switch it has not applied yet).
        runtime_settings.store_json(session, feed.HEARTBEAT_KEY, {"running": True, "last_run_at": datetime.now(UTC).isoformat(), "interval_s": 60, "published": 9, "provider": "matriks", "provider_status": {**seen, "name": "matriks"}, "error": None}, "feed")
        body = c.get("/api/v1/admin/providers", headers=h).json()
        assert body["price"]["status"]["name"] == "yahoo" and body["price"]["status_from"] == "api"

        # Switching to the unconfigured Matriks slot is accepted as a setting but Yahoo stays active.
        r = c.put("/api/v1/admin/settings", json={"price_provider": "matriks", "quotes_interval_s": 15}, headers=h)
        assert r.status_code == 200 and r.json()["changed"] == ["price_provider", "quotes_interval_s"]
        assert settings.price_provider == "matriks" and settings.quotes_interval_s == 15
        body = c.get("/api/v1/admin/providers", headers=h).json()
        assert body["price"]["selected"] == "matriks" and body["price"]["active"] == "yahoo"

        # The other uvicorn worker never saw the PUT: /admin/providers and /quotes re-apply the stored overrides
        # per request, so a stale process-wide `settings` is corrected before answering.
        settings.price_provider, settings.quotes_interval_s = "yahoo", 60  # what the other worker still holds
        body = c.get("/api/v1/admin/providers", headers=h).json()
        assert body["price"]["selected"] == "matriks" and settings.price_provider == "matriks" and settings.quotes_interval_s == 15
        settings.price_provider = "yahoo"
        assert c.get("/api/v1/quotes", headers=h).status_code == 200 and settings.price_provider == "matriks"
        assert c.put("/api/v1/admin/settings", json={"quotes_interval_s": 5}, headers=h).status_code == 400  # below the floor
        assert c.put("/api/v1/admin/settings", json={"price_provider": "bloomberg"}, headers=h).status_code == 400

        # The heartbeat key is storage, not a setting: never listed, never writable through the API.
        r = c.put("/api/v1/admin/settings", json={"_feed_status": {"running": True}}, headers=h)
        assert r.status_code == 400 and "not editable" in r.json()["detail"]
        assert all(s["key"] != feed.HEARTBEAT_KEY for s in c.get("/api/v1/admin/settings", headers=h).json())
        with pytest.raises(runtime_settings.SettingError):
            runtime_settings.store_json(session, "price_provider", "matriks", "feed")  # the helper is for reserved keys only
    finally:
        runtime_settings.set_many(session, {"price_provider": "yahoo", "quotes_interval_s": 60}, "root@example.com")
        app.dependency_overrides.clear()


# --- migration ----------------------------------------------------------------------------------


def test_migration_adds_ohlc_and_source_on_scratch_sqlite(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from instilens.config import BACKEND_ROOT

    url = f"sqlite:///{tmp_path / 'scratch.db'}"
    monkeypatch.setattr(settings, "database_url", url)  # migrations/env.py reads the URL from settings
    cfg = Config()  # no alembic.ini: keeps its logging config away from the test process
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "c9d0e1f2a3b4")  # this revision, not head: later ones (fundamentals) have their own test
    engine = create_engine(url)
    cols = {c["name"]: c for c in inspect(engine).get_columns("market_prices")}
    assert {"open", "high", "low", "close", "volume", "source"} <= set(cols)
    assert cols["source"]["nullable"] is False and "yahoo" in str(cols["source"]["default"])
    with engine.begin() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "c9d0e1f2a3b4"
        conn.execute(text("INSERT INTO market_prices (instrument_id, trade_date, close) VALUES (1, '2026-09-16', 10.5)"))
        assert conn.execute(text("SELECT source, open FROM market_prices")).one() == ("yahoo", None)
    command.downgrade(cfg, "b8c9d0e1f2a3")
    engine.dispose()
    engine = create_engine(url)
    assert {"open", "high", "low", "source"}.isdisjoint(c["name"] for c in inspect(engine).get_columns("market_prices"))
    engine.dispose()
