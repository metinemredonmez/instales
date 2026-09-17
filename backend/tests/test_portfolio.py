"""Portfolios (services/portfolio, /api/v1/portfolios): CRUD, positions derived from transactions with FIFO average
cost, the priced payload (closes, scores, 30-day fund counts, insiders' net for US, weight and P&L maths), the implicit
PORTFOLIO_MOVE alert, and the plan lock. Fixture narrative: THYAO was opened by three funds and added to by one on
2026-08-31, ASELS added to by all five; closes run to 2026-09-14 (ASELS 140, THYAO 315)."""

from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from instilens.config import settings
from instilens.domain.enums import DisclosureKind, Market, Source
from instilens.domain.models import (
    Disclosure,
    InsiderTransaction,
    Notification,
    PortfolioPosition,
    PortfolioTransaction,
)
from instilens.services import alerts, auth, portfolio
from instilens.services.entities import EntityResolver
from tests.conftest import AS_OF


@pytest.fixture(autouse=True)
def _plans_off(monkeypatch):
    monkeypatch.setattr(settings, "plans_enforced", False)


def _client(session, email: str = "p@example.com", plan: str = "PRO") -> tuple[TestClient, dict, int]:
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    u = auth.register(session, email, "password123", "P")
    u.plan, u.plan_source = plan, "manual"
    session.flush()
    return TestClient(app), {"authorization": f"Bearer {auth.issue_token(u)}"}, u.id


def _tx(side: str, qty: str, price: str, day: date, fee: str | None = None, id: int = 0) -> PortfolioTransaction:
    return PortfolioTransaction(id=id, portfolio_id=1, instrument_id=1, side=side, quantity=Decimal(qty), price=Decimal(price), traded_at=day, fee=Decimal(fee) if fee else None)


# --- FIFO -------------------------------------------------------------------------------------------


def test_fifo_average_cost_from_transactions():
    d = date(2026, 8, 1)
    buys = [_tx("BUY", "100", "10", d, fee="5", id=1), _tx("BUY", "100", "20", date(2026, 8, 5), id=2)]
    held, avg, opened = portfolio.fifo(buys)
    assert (held, avg, opened) == (Decimal(200), Decimal("15.025"), d)  # (100×10 + 5 + 100×20) / 200: the buy fee is in the lot
    held, avg, opened = portfolio.fifo(buys + [_tx("SELL", "150", "30", date(2026, 8, 9), fee="9", id=3)])
    assert (held, avg, opened) == (Decimal(50), Decimal(20), date(2026, 8, 5))  # the oldest lot went first; the sale fee never touches the remaining cost
    held, avg, opened = portfolio.fifo(buys + [_tx("SELL", "200", "30", date(2026, 8, 9), id=3)])
    assert (held, avg, opened) == (Decimal(0), None, None)
    with pytest.raises(portfolio.PortfolioError):
        portfolio.fifo(buys + [_tx("SELL", "201", "30", date(2026, 8, 9), id=3)])
    with pytest.raises(portfolio.PortfolioError):  # order is by trade date, not by entry: a sale dated before the buys oversells
        portfolio.fifo(buys + [_tx("SELL", "1", "30", date(2026, 7, 1), id=3)])
    held, avg, _ = portfolio.fifo([_tx("BUY", "1.5", "100", d, id=1), _tx("BUY", "0.5", "200", d, id=2)])  # fractional shares
    assert (held, avg) == (Decimal(2), Decimal(125))
    # Same day: recorded order; a row not yet written (no id) runs last on its day, so a day trade's sale closes its buy.
    held, avg, opened = portfolio.fifo(buys + [_tx("SELL", "100", "30", d, id=None)])
    assert (held, avg, opened) == (Decimal(100), Decimal(20), date(2026, 8, 5))
    with pytest.raises(portfolio.PortfolioError):  # nothing held yet on that day: the sale still oversells
        portfolio.fifo([_tx("SELL", "1", "30", d, id=None), _tx("BUY", "1", "30", date(2026, 8, 2), id=None)])


# --- CRUD, derived positions, the payload -----------------------------------------------------------


def test_crud_transactions_and_priced_payload(session, pipeline_run):
    c, h, _ = _client(session)
    assert c.get("/api/v1/portfolios", headers=h).json() == []
    r = c.post("/api/v1/portfolios", json={"name": "  Uzun  vade ", "market": "TR"}, headers=h)
    assert r.status_code == 201 and r.json()["name"] == "Uzun vade" and r.json()["currency"] == "TRY"
    pid = r.json()["id"]
    assert c.post("/api/v1/portfolios", json={"name": "x", "market": "XX"}, headers=h).status_code == 422
    renamed = c.patch(f"/api/v1/portfolios/{pid}", json={"name": "Ana"}, headers=h).json()
    assert (renamed["name"], renamed["market"], renamed["currency"], renamed["positions"]) == ("Ana", "TR", "TRY", 0)  # the list's shape, as create answers
    assert c.get("/api/v1/portfolios/999", headers=h).status_code == 404

    # A position entered by hand, and one derived from transactions.
    r = c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": "asels", "quantity": 100, "avg_cost": "150", "opened_at": "2026-06-01", "note": "uzun vade"}, headers=h)
    assert r.status_code == 201 and r.json()["symbol"] == "ASELS" and r.json()["avg_cost"] == 150 and r.json()["derived"] is False
    assert c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": "NOPE", "quantity": 1}, headers=h).status_code == 400
    assert c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": "ASELS", "quantity": 0}, headers=h).status_code == 400
    assert c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": "ASELS", "quantity": "abc"}, headers=h).status_code == 400
    assert c.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "THYAO", "side": "buy", "quantity": 10, "price": 100, "traded_at": "2026-08-01"}, headers=h).status_code == 201
    assert c.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "THYAO", "side": "BUY", "quantity": 10, "price": 120, "traded_at": "2026-08-10", "fee": 0}, headers=h).status_code == 201
    r = c.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "THYAO", "side": "SELL", "quantity": 5, "price": 130, "traded_at": "2026-09-01"}, headers=h)
    assert r.status_code == 201
    sell_id = r.json()["id"]
    assert (r.json()["symbol"], r.json()["side"], r.json()["quantity"], r.json()["price"], r.json()["traded_at"]) == ("THYAO", "SELL", 5, 130, "2026-09-01")
    assert c.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "THYAO", "side": "SELL", "quantity": 50, "price": 130, "traded_at": "2026-09-02"}, headers=h).status_code == 400  # oversell: nothing written
    assert c.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "THYAO", "side": "SELL", "quantity": 1, "price": 130, "traded_at": "2999-01-01"}, headers=h).status_code == 400
    assert c.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "THYAO", "side": "HOLD", "quantity": 1, "price": 1, "traded_at": "2026-09-02"}, headers=h).status_code == 422
    r = c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": "THYAO", "quantity": 1}, headers=h)
    assert r.status_code == 409 and "derived" in r.json()["detail"]  # a derived position is not edited by hand

    d = c.get(f"/api/v1/portfolios/{pid}", headers=h).json()
    assert d["portfolio"]["positions"] == 2 and d["as_of"] == AS_OF.isoformat()
    by = {p["symbol"]: p for p in d["positions"]}
    asels, thyao = by["ASELS"], by["THYAO"]
    assert asels["derived"] is False and asels["quantity"] == 100 and asels["avg_cost"] == 150 and asels["opened_at"] == "2026-06-01" and asels["note"] == "uzun vade"
    assert (asels["last_close"], asels["close_date"]) == (140, "2026-09-14")
    assert (asels["market_value"], asels["cost_value"], asels["pnl_value"], asels["pnl_pct"]) == (14000, 15000, -1000, -6.67)
    assert thyao["derived"] is True and thyao["quantity"] == 15 and thyao["avg_cost"] == 113.333333 and thyao["opened_at"] == "2026-08-01"  # FIFO: 5×100 + 10×120 over 15
    assert (thyao["last_close"], thyao["market_value"], thyao["cost_value"]) == (315, 4725, 1700)
    assert thyao["pnl_value"] == 3025 and thyao["pnl_pct"] == round(3025 / 1700 * 100, 2)
    assert asels["weight_pct"] == round(14000 / 18725 * 100, 2) and thyao["weight_pct"] == round(4725 / 18725 * 100, 2)
    assert d["totals"] == {"market_value": 18725, "cost_value": 16700, "pnl_value": 2025, "pnl_pct": round(2025 / 16700 * 100, 2), "unpriced": 0}
    assert asels["smart_money_score"] is not None and asels["consensus_score"] is not None and asels["crowding_score"] is not None
    # ASELS: five funds added on 08-31 and İş Portföy's GROUPED sale of 09-12 (one institution party) is uncovered by any later snapshot.
    assert (asels["funds_increasing_30d"], asels["funds_reducing_30d"]) == (5, 1) and (thyao["funds_increasing_30d"], thyao["funds_reducing_30d"]) == (4, 0)
    assert asels["insiders_net_90d"] is None and thyao["insiders_net_90d"] is None  # TR: no Form 4
    assert d["moves"]["window_days"] == 30 and {m["symbol"] for m in d["moves"]["rows"]} == {"ASELS", "THYAO"}
    asels_move = next(m for m in d["moves"]["rows"] if m["symbol"] == "ASELS")
    assert (asels_move["funds_increasing"], asels_move["funds_reducing"], asels_move["party_count"], len(asels_move["parties"])) == (5, 1, 6, 5)  # at most five parties named, largest first
    assert all({"kind", "code", "name", "activity", "delta_qty", "confidence"} <= set(p) for p in asels_move["parties"])
    assert [t["side"] for t in d["transactions"]] == ["SELL", "BUY", "BUY"] and d["transactions"][0]["symbol"] == "THYAO"
    assert all(k in asels for k in ("symbol", "name", "quantity", "avg_cost", "last_close", "close_date", "market_value", "cost_value", "pnl_value", "pnl_pct", "weight_pct",
                                    "smart_money_score", "consensus_score", "crowding_score", "funds_increasing_30d", "funds_reducing_30d", "insiders_net_90d"))

    # Removing the sale rederives; removing a position drops its transactions; deleting the portfolio takes everything.
    assert c.delete(f"/api/v1/portfolios/{pid}/transactions/{sell_id}", headers=h).status_code == 204
    thyao = next(p for p in c.get(f"/api/v1/portfolios/{pid}", headers=h).json()["positions"] if p["symbol"] == "THYAO")
    assert (thyao["quantity"], thyao["avg_cost"]) == (20, 110)
    assert c.delete(f"/api/v1/portfolios/{pid}/transactions/999", headers=h).status_code == 404
    assert c.delete(f"/api/v1/portfolios/{pid}/positions/{thyao['id']}", headers=h).status_code == 204
    assert session.scalar(select(PortfolioTransaction).where(PortfolioTransaction.portfolio_id == pid)) is None
    assert c.get(f"/api/v1/portfolios/{pid}", headers=h).json()["portfolio"]["positions"] == 1
    # A full sale removes the position but keeps the history; a re-entered position without a cost is unpriced for P&L.
    assert c.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "SASA", "side": "BUY", "quantity": 3, "price": 4, "traded_at": "2026-08-20"}, headers=h).status_code == 201
    assert c.post(f"/api/v1/portfolios/{pid}/transactions", json={"symbol": "SASA", "side": "SELL", "quantity": 3, "price": 3.5, "traded_at": "2026-08-25"}, headers=h).status_code == 201
    d = c.get(f"/api/v1/portfolios/{pid}", headers=h).json()
    assert {p["symbol"] for p in d["positions"]} == {"ASELS"} and [t["symbol"] for t in d["transactions"]] == ["SASA", "SASA"]
    assert c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": "KCHOL", "quantity": 2}, headers=h).status_code == 201
    d = c.get(f"/api/v1/portfolios/{pid}", headers=h).json()
    kchol = next(p for p in d["positions"] if p["symbol"] == "KCHOL")
    assert kchol["market_value"] == 430 and kchol["cost_value"] is None and kchol["pnl_value"] is None and d["totals"]["cost_value"] == 15000 and d["totals"]["pnl_value"] == -1000
    assert kchol["weight_pct"] == round(430 / 14430 * 100, 2)
    assert c.delete(f"/api/v1/portfolios/{pid}", headers=h).status_code == 204
    assert c.get("/api/v1/portfolios", headers=h).json() == [] and session.scalar(select(PortfolioPosition)) is None
    # Another account never sees it.
    c2, h2, _ = _client(session, "other@example.com")
    assert c2.get(f"/api/v1/portfolios/{pid}", headers=h2).status_code == 404
    from instilens.api.main import app

    app.dependency_overrides.clear()


def test_same_day_trades_and_sub_scale_amounts(session, pipeline_run):
    """A day trade: the sale recorded after the same day's buy closes it; a sale on a day nothing is held is still
    refused. An amount below the column's scale is nothing — 0.00001 lots would store as 0 and still count."""
    c, h, _ = _client(session)
    pid = c.post("/api/v1/portfolios", json={"name": "Gün içi", "market": "TR"}, headers=h).json()["id"]
    tx = lambda body: c.post(f"/api/v1/portfolios/{pid}/transactions", json={"traded_at": "2026-08-01", "price": 50, **body}, headers=h).status_code  # noqa: E731
    assert tx({"symbol": "EREGL", "side": "SELL", "quantity": 5}) == 400
    assert tx({"symbol": "EREGL", "side": "BUY", "quantity": 10}) == 201
    assert tx({"symbol": "EREGL", "side": "SELL", "quantity": 4, "price": 52}) == 201
    d = c.get(f"/api/v1/portfolios/{pid}", headers=h).json()
    assert d["positions"][0]["symbol"] == "EREGL" and d["positions"][0]["quantity"] == 6 and d["positions"][0]["avg_cost"] == 50
    assert tx({"symbol": "EREGL", "side": "SELL", "quantity": 6, "price": 53}) == 201
    assert c.get(f"/api/v1/portfolios/{pid}", headers=h).json()["positions"] == []  # sold flat the same day; the rows stay
    assert tx({"symbol": "EREGL", "side": "SELL", "quantity": 1}) == 400
    assert c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": "KCHOL", "quantity": 0.00001}, headers=h).status_code == 400
    assert tx({"symbol": "KCHOL", "side": "BUY", "quantity": "0.00004"}) == 400
    assert tx({"symbol": "KCHOL", "side": "BUY", "quantity": "0.00005"}) == 201  # rounds to the scale's first step
    assert c.get(f"/api/v1/portfolios/{pid}", headers=h).json()["positions"][0]["quantity"] == 0.0001
    from instilens.api.main import app

    app.dependency_overrides.clear()


def test_us_position_carries_the_insiders_net(session, pipeline_run):
    resolver = EntityResolver(session)
    oxy = resolver.instrument(Market.US, "OXY")
    oxy.sec_form4_fetched_at = datetime.now(UTC)
    disc = Disclosure(market_code="US", source=Source.SEC, source_id="0000000000-26-000001", kind=DisclosureKind.SEC_FORM4, published_at=datetime(2026, 9, 1), raw_hash="h", payload={}, parse_status="PARSED")
    session.add(disc)
    session.flush()
    for i, (code, price) in enumerate((("P", "50"), ("S", "20"))):
        session.add(InsiderTransaction(disclosure_id=disc.id, instrument_id=oxy.id, insider_cik=f"200{i}", insider_name=f"Insider {i}", roles="director", title=None,
                                       transaction_date=date(2026, 9, 1), filed_at=datetime(2026, 9, 2), code=code, acquired=code == "P", shares=Decimal(100), price=Decimal(price),
                                       post_shares=None, ownership="D", derivative=False, row_hash=f"t-{i}"))
    session.flush()
    c, h, _ = _client(session)
    pid = c.post("/api/v1/portfolios", json={"name": "US", "market": "US"}, headers=h).json()["id"]
    assert c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": "OXY", "quantity": 10, "avg_cost": 50}, headers=h).status_code == 201
    assert c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": "ASELS", "quantity": 10}, headers=h).status_code == 400  # a TR symbol is not on the US market
    d = c.get(f"/api/v1/portfolios/{pid}", headers=h).json()
    (pos,) = d["positions"]
    assert pos["insiders_net_90d"] == 3000  # 100×50 bought − 100×20 sold, Form 4 open-market rows only
    assert pos["last_close"] is None and pos["market_value"] is None and d["totals"] == {"market_value": None, "cost_value": 500, "pnl_value": None, "pnl_pct": None, "unpriced": 1}
    assert d["portfolio"]["currency"] == "USD"
    from instilens.api.main import app

    app.dependency_overrides.clear()


# --- the implicit alert -----------------------------------------------------------------------------


def test_portfolio_move_alert_fires_once_and_respects_the_plan(session, pipeline_run, monkeypatch):
    c, h, uid = _client(session)
    pid = c.post("/api/v1/portfolios", json={"name": "Ana", "market": "TR"}, headers=h).json()["id"]
    for symbol in ("THYAO", "KCHOL", "SASA"):
        assert c.put(f"/api/v1/portfolios/{pid}/positions", json={"symbol": symbol, "quantity": 10}, headers=h).status_code == 201
    pid2 = c.post("/api/v1/portfolios", json={"name": "İkinci", "market": "TR"}, headers=h).json()["id"]  # the same symbol in two portfolios: one notification
    assert c.put(f"/api/v1/portfolios/{pid2}/positions", json={"symbol": "THYAO", "quantity": 1}, headers=h).status_code == 201
    assert alerts.evaluate(session, AS_OF) == 2  # THYAO: 3 NEW + 1 ADD; SASA: 3 EXIT + 1 REDUCE; KCHOL: every fund held
    assert alerts.evaluate(session, AS_OF) == 0
    notes = {n.title.split(":")[0]: n for n in session.scalars(select(Notification).where(Notification.owner_id == str(uid)))}
    thyao, sasa = notes["Portföyündeki THYAO"], notes["Portföyündeki SASA"]
    assert thyao.title == "Portföyündeki THYAO: son dönemde 4 fon artırdı" and thyao.link == "/portfolio"
    assert thyao.body == "dönem sonu 2026-08-31 · yeni 3 · artıran 1 · azaltan 0 · çıkan 0 · IPB, TI2, TLY, TMV"
    assert sasa.title == "Portföyündeki SASA: son dönemde 1 fon azalttı, 3 fon çıktı"
    assert thyao.dedup_key.startswith("pf:") and thyao.dedup_key.endswith(":PORTFOLIO_MOVE:2026-08-31") and thyao.alert_rule_id is None
    assert "buy" not in thyao.title.lower() and "sell" not in sasa.title.lower()
    assert "PORTFOLIO_MOVE" not in alerts.RULE_TYPES  # never an explicit rule
    assert c.post("/api/v1/alerts/rules", json={"symbol": "THYAO", "rule_type": "PORTFOLIO_MOVE"}, headers=h).status_code == 400

    # English text for an English account.
    from instilens.domain.models import User

    session.get(User, uid).lang = "en"
    session.flush()
    for n in list(notes.values()):
        session.delete(n)
    session.flush()
    assert alerts.evaluate(session, AS_OF) == 2
    titles = sorted(n.title for n in session.scalars(select(Notification).where(Notification.owner_id == str(uid))))
    assert titles == ["SASA in your portfolio: 1 fund reduced, 3 funds exited in the latest period", "THYAO in your portfolio: 4 funds increased in the latest period"]

    # A locked portfolio (plan without the feature, gating on) is silent; the lock lifts with the plan or the switch.
    monkeypatch.setattr(settings, "plans_enforced", True)
    session.get(User, uid).plan = "FREE"
    for n in session.scalars(select(Notification).where(Notification.owner_id == str(uid))):
        session.delete(n)
    session.flush()
    assert alerts.evaluate(session, AS_OF) == 0
    assert c.get(f"/api/v1/portfolios/{pid}", headers=h).status_code == 402
    session.get(User, uid).plan = "PRO"
    session.flush()
    assert alerts.evaluate(session, AS_OF) == 2
    from instilens.api.main import app

    app.dependency_overrides.clear()
