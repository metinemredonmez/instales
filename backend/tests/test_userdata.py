from datetime import date

from fastapi.testclient import TestClient

from instilens.services import alerts, auth
from tests.conftest import AS_OF


def _client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    c = TestClient(app)
    token = auth.issue_token(auth.register(session, "w@example.com", "password123", "W"))
    c.headers["authorization"] = f"Bearer {token}"
    return c


def test_watchlist_crud(session, pipeline_run):
    c = _client(session)
    assert c.post("/api/v1/watchlist", json={"symbol": "ASELS"}).status_code == 201
    assert c.post("/api/v1/watchlist", json={"symbol": "ASELS"}).json()["created"] is False  # idempotent
    assert c.post("/api/v1/watchlist", json={"fund_code": "TMV"}).status_code == 201
    assert c.post("/api/v1/watchlist", json={"symbol": "NOPE"}).status_code == 404
    items = c.get("/api/v1/watchlist").json()
    assert [(i["kind"], i["ref"]) for i in items] == [("stock", "ASELS"), ("fund", "TMV")]
    assert items[0]["smart_money_score"] is not None
    assert c.delete(f"/api/v1/watchlist/{items[0]['id']}").status_code == 204
    assert len(c.get("/api/v1/watchlist").json()) == 1


def test_alert_rules_fire_once(session, pipeline_run):
    c = _client(session)
    assert c.post("/api/v1/alerts/rules", json={"symbol": "THYAO", "rule_type": "NEW_FUND_POSITION"}).status_code == 201
    assert c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "SIGNAL", "params": {"types": ["POSITIVE_DIVERGENCE"]}}).status_code == 201
    assert c.post("/api/v1/alerts/rules", json={"symbol": "SASA", "rule_type": "SCORE_ABOVE", "params": {"threshold": 80}}).status_code == 201
    assert c.post("/api/v1/alerts/rules", json={"fund_code": "TMV", "rule_type": "FUND_ACTIVITY"}).status_code == 201
    assert c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "BOGUS"}).status_code == 400

    first = alerts.evaluate(session, AS_OF)
    again = alerts.evaluate(session, AS_OF)
    assert first == 3 and again == 0  # THYAO new-position, ASELS divergence, TMV activity; SASA score too low
    notes = c.get("/api/v1/alerts/notifications").json()
    titles = {n["title"].split(":")[0] for n in notes}
    assert titles == {"THYAO", "ASELS", "TMV"}
    assert all(n["read_at"] is None for n in notes)
    assert c.post("/api/v1/alerts/notifications/read").json()["marked"] == 3

    rule_id = c.get("/api/v1/alerts/rules").json()["rules"][0]["id"]
    assert c.delete(f"/api/v1/alerts/rules/{rule_id}").status_code == 204
    assert c.get("/api/v1/alerts/rules").json()["rules"][0]["is_active"] is False


def test_stock_series(session, pipeline_run):
    c = _client(session)
    s = c.get("/api/v1/stocks/ASELS/series").json()
    assert len(s["prices"]) >= 5 and s["holdings"][-1]["funds"] == 5
    assert s["holdings"][0]["quantity"] < s["holdings"][-1]["quantity"]


def test_new_read_models(session, pipeline_run):
    from instilens.services import analytics

    cmp = analytics.compare_funds(session, "TMV", "MAC")
    assert cmp["overlap_pct"] > 0 and "ASELS" in cmp["both_increasing"] and "SASA" in cmp["both_reducing"]
    tl = analytics.stock_timeline(session, "TR", "ASELS")
    assert [i["kind"] for i in tl].count("PERIOD") == 3 and any(i["kind"] == "SIGNAL" for i in tl)
    assert tl == sorted(tl, key=lambda i: i["date"])
    inst = analytics.institution_detail(session, "TR", "TERA_PORTFÖY_YÖNETİMİ_AŞ") or analytics.institution_detail(session, "TR", analytics.institutions(session, "TR")[-1]["code"])
    assert inst and inst["funds"]
    w7 = analytics.window_flows(session, "TR", 7, date(2026, 9, 14))
    assert {r["symbol"] for r in w7["accumulated"]} == {"ANELE", "THYAO"}  # only post-snapshot events fall in 7D
    assert analytics.window_flows(session, "TR", 1, date(2026, 9, 14))["accumulated"] == []
    fresh = analytics.data_freshness(session, "TR")
    assert fresh[0]["last"].startswith("2026-09-12")
    from instilens.services.outcomes import compute_outcomes

    assert compute_outcomes(session) == 6
    perf = analytics.signal_performance(session, "TR")
    assert {p["signal_type"] for p in perf["by_type"]} >= {"ACCUMULATION", "POSITIVE_DIVERGENCE"}
