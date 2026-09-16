from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from instilens.config import settings
from instilens.services import auth, quotes, runtime_settings

IST = ZoneInfo("Europe/Istanbul")
NY = ZoneInfo("America/New_York")


def _at(y, m, d, hh, mm, tz):
    return datetime(y, m, d, hh, mm, tzinfo=tz)


@pytest.fixture(autouse=True)
def _clean_quote_cache():
    """The module-level cache and last-good memory must not leak between tests, even when one fails mid-way."""
    quotes.reset()
    yield
    quotes.reset()


# --- market hours (pure functions, fixed clock) ---------------------------------------------


def test_bist_weekday_open():
    st = quotes.market_state("TR", _at(2026, 9, 16, 12, 0, IST))  # Wednesday noon
    assert st["state"] == "open" and st["label_key"] == "market.open" and st["tz"] == "Europe/Istanbul"
    assert datetime.fromisoformat(st["next_change_at"]) == _at(2026, 9, 16, 18, 0, IST)


def test_bist_weekday_after_close_points_to_next_morning():
    st = quotes.market_state("TR", _at(2026, 9, 16, 18, 30, IST))
    assert st["state"] == "closed" and st["label_key"] == "market.closed"
    assert datetime.fromisoformat(st["next_change_at"]) == _at(2026, 9, 17, 10, 0, IST)


def test_bist_before_open_points_to_today():
    st = quotes.market_state("TR", _at(2026, 9, 16, 9, 59, IST))
    assert st["state"] == "closed"
    assert datetime.fromisoformat(st["next_change_at"]) == _at(2026, 9, 16, 10, 0, IST)


def test_bist_saturday_skips_to_monday():
    st = quotes.market_state("TR", _at(2026, 9, 19, 12, 0, IST))  # Saturday
    assert st["state"] == "closed"
    assert datetime.fromisoformat(st["next_change_at"]) == _at(2026, 9, 21, 10, 0, IST)


def test_bist_holiday_escape_hatch():
    # 29 October 2026 is a Thursday; declared a holiday it is closed and the next open is Friday.
    st = quotes.market_state("TR", _at(2026, 10, 29, 12, 0, IST), holidays=[date(2026, 10, 29)])
    assert st["state"] == "closed"
    assert datetime.fromisoformat(st["next_change_at"]) == _at(2026, 10, 30, 10, 0, IST)
    # Consecutive holidays are skipped too.
    st = quotes.market_state("TR", _at(2026, 10, 29, 12, 0, IST), holidays=[date(2026, 10, 29), date(2026, 10, 30)])
    assert datetime.fromisoformat(st["next_change_at"]) == _at(2026, 11, 2, 10, 0, IST)


def test_nyse_pre_open_post_and_utc_input():
    pre = quotes.market_state("US", _at(2026, 9, 16, 8, 0, NY))
    assert pre["state"] == "pre" and pre["label_key"] == "market.pre"
    assert datetime.fromisoformat(pre["next_change_at"]) == _at(2026, 9, 16, 9, 30, NY)
    # 14:00 UTC on 16 Sep 2026 (EDT) is 10:00 New York — the core session.
    core = quotes.market_state("US", _at(2026, 9, 16, 14, 0, UTC))
    assert core["state"] == "open" and core["tz"] == "America/New_York"
    assert datetime.fromisoformat(core["next_change_at"]) == _at(2026, 9, 16, 16, 0, NY)
    post = quotes.market_state("US", _at(2026, 9, 16, 19, 59, NY))
    assert post["state"] == "post"
    assert datetime.fromisoformat(post["next_change_at"]) == _at(2026, 9, 16, 20, 0, NY)
    late = quotes.market_state("US", _at(2026, 9, 18, 21, 0, NY))  # Friday night → Monday pre-market
    assert late["state"] == "closed"
    assert datetime.fromisoformat(late["next_change_at"]) == _at(2026, 9, 21, 4, 0, NY)


def test_nyse_standard_time_and_dst_transitions():
    # 16 December 2026 (EST): 15:00 UTC is 10:00 New York — open, closing at 16:00 EST = 21:00 UTC.
    winter = quotes.market_state("US", _at(2026, 12, 16, 15, 0, UTC))
    assert winter["state"] == "open"
    nxt = datetime.fromisoformat(winter["next_change_at"])
    assert nxt == _at(2026, 12, 16, 21, 0, UTC) and nxt.utcoffset() == timedelta(hours=-5)
    # Sunday 1 November 2026 (clocks fall back that morning): closed, next pre-market Monday 04:00 EST.
    fall = quotes.market_state("US", _at(2026, 11, 1, 12, 0, NY))
    assert fall["state"] == "closed"
    nxt = datetime.fromisoformat(fall["next_change_at"])
    assert nxt == _at(2026, 11, 2, 4, 0, NY) and nxt.utcoffset() == timedelta(hours=-5)
    # Sunday 8 March 2026 (clocks spring forward): next pre-market Monday 04:00 EDT.
    spring = quotes.market_state("US", _at(2026, 3, 8, 12, 0, NY))
    nxt = datetime.fromisoformat(spring["next_change_at"])
    assert nxt == _at(2026, 3, 9, 4, 0, NY) and nxt.utcoffset() == timedelta(hours=-4)


def test_naive_clock_is_rejected():
    try:
        quotes.market_state("TR", datetime(2026, 9, 16, 12, 0))
    except ValueError:
        return
    raise AssertionError("naive datetime accepted")


def test_holiday_setting_parsing_and_coercion(monkeypatch):
    monkeypatch.setattr(settings, "market_holidays_tr", ["2026-10-29", " 2027-01-01 ", "not-a-date"])
    assert quotes.tr_holidays() == [date(2026, 10, 29), date(2027, 1, 1)]
    assert runtime_settings.coerce("market_holidays_tr", "2027-01-01, 2026-10-29") == ["2026-10-29", "2027-01-01"]
    try:
        runtime_settings.coerce("market_holidays_tr", ["2026-13-01"])
    except runtime_settings.SettingError:
        return
    raise AssertionError("non-date accepted")


# --- quotes (Yahoo monkeypatched) ------------------------------------------------------------


def _frame(closes: dict[str, list[float]]) -> pd.DataFrame:
    """A yf.download(group_by='ticker') shaped frame: columns (ticker, field)."""
    idx = pd.date_range("2026-09-10", periods=max(len(v) for v in closes.values()), freq="D")
    cols, data = [], {}
    for tk, vals in closes.items():
        padded = [None] * (len(idx) - len(vals)) + list(vals)
        for field in ("Open", "Close"):
            cols.append((tk, field))
            data[(tk, field)] = padded
    return pd.DataFrame(data, index=idx, columns=pd.MultiIndex.from_tuples(cols))


KNOWN = {"USDTRY=X": [41.0, 41.41], "EURTRY=X": [48.0, 48.24], "XU100.IS": [10000.0, 10150.4], "^GSPC": [6500.0, 6435.0]}


def test_snapshot_parses_frame_caches_and_marks_stale(monkeypatch):
    calls = []

    def fake(tickers):
        calls.append(list(tickers))
        return _frame(KNOWN)

    monkeypatch.setattr(quotes, "fetch_frame", fake)
    t0 = _at(2026, 9, 16, 12, 0, UTC)
    out = quotes.snapshot(now=t0)
    assert calls == [["USDTRY=X", "EURTRY=X", "XU100.IS", "^GSPC"]]
    by_key = {q["key"]: q for q in out["quotes"]}
    assert [q["key"] for q in out["quotes"]] == ["USDTRY", "EURTRY", "XU100", "SPX"]
    assert by_key["USDTRY"] == {"key": "USDTRY", "label": "USD/TRY", "price": 41.41, "change_pct": 1.0, "currency": "TRY", "updated_at": t0.isoformat(), "decimals": 4, "bar_date": "2026-09-11"}
    assert by_key["XU100"]["price"] == 10150 and by_key["XU100"]["change_pct"] == 1.5 and by_key["XU100"]["currency"] == "TRY"
    assert by_key["SPX"]["price"] == 6435 and by_key["SPX"]["change_pct"] == -1.0 and by_key["SPX"]["currency"] == "USD"
    assert all("stale" not in q for q in out["quotes"])
    assert out["as_of"] == t0.isoformat() and set(out["markets"]) == {"TR", "US"}
    assert out["markets"]["TR"]["state"] == "open" and out["markets"]["US"]["state"] == "pre"  # 15:00 Istanbul / 08:00 New York

    # Within 60 s the cache answers; after it a new fetch happens.
    quotes.snapshot(now=t0 + timedelta(seconds=59))
    assert len(calls) == 1
    quotes.snapshot(now=t0 + timedelta(seconds=60))
    assert len(calls) == 2

    # Yahoo down: values are carried over flagged stale with the OLD updated_at, never re-dated.
    monkeypatch.setattr(quotes, "fetch_frame", lambda tickers: (_ for _ in ()).throw(OSError("yahoo down")))
    t1 = t0 + timedelta(seconds=200)
    stale = {q["key"]: q for q in quotes.snapshot(now=t1)["quotes"]}
    assert len(stale) == 4 and all(q["stale"] is True for q in stale.values())
    assert stale["USDTRY"]["updated_at"] == (t0 + timedelta(seconds=60)).isoformat() and stale["USDTRY"]["price"] == 41.41


def test_snapshot_omits_unknown_tickers_and_single_close_has_no_change(monkeypatch):
    monkeypatch.setattr(quotes, "fetch_frame", lambda tickers: _frame({"USDTRY=X": [41.41], "XU100.IS": [10000.0, 10150.4]}))
    out = quotes.snapshot(now=_at(2026, 9, 16, 12, 0, UTC))
    assert [q["key"] for q in out["quotes"]] == ["USDTRY", "XU100"]  # EUR/TRY and S&P never seen → omitted
    assert out["quotes"][0]["change_pct"] is None and out["quotes"][0]["price"] == 41.41

    # Total outage with no history → an empty list, not invented numbers.
    quotes.reset()
    monkeypatch.setattr(quotes, "fetch_frame", lambda tickers: None)
    assert quotes.snapshot(now=_at(2026, 9, 16, 12, 5, UTC))["quotes"] == []


def test_missing_ticker_in_grouped_frame_is_omitted_not_a_frame():
    frame = _frame({"USDTRY=X": [41.0, 41.41]})
    assert quotes._closes(frame, "^GSPC") is None  # never the whole "Close" sub-frame
    assert quotes.parse_quote(frame, "^GSPC") is None
    assert quotes.parse_quote(frame, "USDTRY=X").bar_date == date(2026, 9, 11)
    # A flat single-ticker frame (no column grouping) still resolves its plain Close column.
    flat = pd.DataFrame({"Open": [1.0, 1.0], "Close": [40.0, 42.0]}, index=pd.date_range("2026-09-10", periods=2, freq="D"))
    assert quotes.parse_quote(flat, "USDTRY=X").price == 42.0


def test_parse_failure_drops_one_ticker_not_the_payload(monkeypatch):
    monkeypatch.setattr(quotes, "fetch_frame", lambda tickers: _frame(KNOWN))
    real = quotes.parse_quote

    def flaky(frame, ticker):
        if ticker == "^GSPC":
            raise TypeError("float() argument must be a string or a real number, not 'DataFrame'")
        return real(frame, ticker)

    monkeypatch.setattr(quotes, "parse_quote", flaky)
    out = quotes.snapshot(now=_at(2026, 9, 16, 12, 0, UTC))
    assert [q["key"] for q in out["quotes"]] == ["USDTRY", "EURTRY", "XU100"]


def test_quotes_route_requires_auth_and_serves_snapshot(session, monkeypatch):
    from instilens.api import deps
    from instilens.api.main import app

    monkeypatch.setattr(quotes, "fetch_frame", lambda tickers: _frame(KNOWN))
    app.dependency_overrides[deps.get_session] = lambda: session
    c = TestClient(app)
    assert c.get("/api/v1/quotes").status_code == 401
    token = auth.issue_token(auth.register(session, "q@example.com", "password123", "Q"))
    r = c.get("/api/v1/quotes", headers={"authorization": f"Bearer {token}"})
    assert r.status_code == 200
    body = r.json()
    assert {q["key"]: q["price"] for q in body["quotes"]} == {"USDTRY": 41.41, "EURTRY": 48.24, "XU100": 10150, "SPX": 6435}
    assert body["markets"]["TR"]["tz"] == "Europe/Istanbul" and body["markets"]["US"]["state"] in ("open", "closed", "pre", "post")
    datetime.fromisoformat(body["as_of"])
    app.dependency_overrides.clear()
