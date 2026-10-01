"""Coverage gates, Decimal basket math, source lineage and owner/plan boundaries."""

from datetime import datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from instilens.config import settings
from instilens.domain.enums import Confidence, Market
from instilens.domain.models import (
    Disclosure,
    Fund,
    MarketPrice,
    Portfolio,
    PortfolioPosition,
    PortfolioSnapshot,
    SnapshotHolding,
)
from instilens.services import auth, portfolio_analysis
from instilens.services.entities import EntityResolver
from tests.conftest import AS_OF


def _basket(session, count=2):
    resolver = EntityResolver(session)
    instruments = [resolver.instrument(Market.TR, f"STOCK{i}") for i in range(count)]
    p = Portfolio(owner_id="1", name="Basket", market_code="TR", currency="TRY")
    session.add(p)
    session.flush()
    for instrument in instruments:
        session.add(PortfolioPosition(portfolio_id=p.id, instrument_id=instrument.id, quantity=10))
    session.flush()
    return p, instruments


def _history(session, instruments, days=23):
    dates, cursor = [], AS_OF
    while len(dates) < days:
        if cursor.weekday() < 5:
            dates.append(cursor)
        cursor -= timedelta(days=1)
    dates.reverse()
    for i, instrument in enumerate(instruments):
        for day in dates:
            session.add(MarketPrice(instrument_id=instrument.id, trade_date=day,
                                    close=Decimal(100 * (i + 1)), source="csv"))
    session.flush()
    return dates


def test_decimal_math_known_peak_and_loss():
    metrics = portfolio_analysis.basket_metrics([Decimal(100), Decimal(80), Decimal(120)])
    assert metrics["price_change_pct"] == 20
    assert metrics["max_drawdown_pct"] == 20
    # Returns -0.2 and +0.5: sample variance 0.245; sqrt(0.245*252)*100.
    assert metrics["annualized_volatility_pct"] == float(round(Decimal("61.74").sqrt() * 100, 2))


def test_concentration_and_constant_basket_with_price_sources(session):
    p, instruments = _basket(session)
    _history(session, instruments)
    d = portfolio_analysis.analyze(session, p, AS_OF)
    c = d["concentration"]
    assert c["largest_weight_pct"] == 66.67 and c["top3_weight_pct"] == 100
    assert c["hhi"] == 5555.56 and c["priced_positions"] == 2
    assert c["allocations"][0] == {"symbol": "STOCK1", "weight_pct": 66.67}
    assert d["risk"]["status"] == "ready"
    assert d["risk"]["price_change_pct"] == d["risk"]["max_drawdown_pct"] == d["risk"]["annualized_volatility_pct"] == 0
    assert d["price_sources"][0]["providers"] == ["csv"]
    assert d["price_sources"][0]["instrument_id"] == instruments[0].id
    # Future quotes and another market's identically named instrument cannot affect this basket.
    foreign = EntityResolver(session).instrument(Market.US, "STOCK0")
    session.add(MarketPrice(instrument_id=foreign.id, trade_date=AS_OF, close=Decimal(999999), source="csv"))
    session.add(MarketPrice(instrument_id=instruments[0].id, trade_date=AS_OF + timedelta(days=1), close=Decimal(999999), source="csv"))
    session.flush()
    assert portfolio_analysis.analyze(session, p, AS_OF) == d


@pytest.mark.parametrize("problem,status", [
    ("missing", "missing_prices"), ("short", "insufficient_history"),
    ("stale", "stale_prices"), ("gap", "incomplete_history"),
    ("zero", "invalid_prices"), ("negative", "invalid_prices"),
    ("long_gap", "incomplete_history"),
])
def test_incomplete_data_never_becomes_zero_risk(session, problem, status):
    p, instruments = _basket(session)
    dates = _history(session, instruments[:1] if problem == "missing" else instruments,
                     days=10 if problem == "short" else 23)
    on = AS_OF + timedelta(days=8) if problem == "stale" else AS_OF
    if problem == "gap":
        session.delete(session.get(MarketPrice, (instruments[1].id, dates[5])))
    if problem in ("zero", "negative"):
        session.get(MarketPrice, (instruments[0].id, dates[5])).close = Decimal(0 if problem == "zero" else -1)
    if problem == "long_gap":
        for instrument in instruments:
            for day in dates[3:12]:
                session.delete(session.get(MarketPrice, (instrument.id, day)))
    session.flush()
    d = portfolio_analysis.analyze(session, p, on)
    assert d["risk"]["status"] == status
    assert all(d["risk"][key] is None for key in ("price_change_pct", "annualized_volatility_pct", "max_drawdown_pct"))
    if problem == "missing":
        assert d["concentration"]["missing_symbols"] == ["STOCK1"]
        assert d["concentration"]["largest_weight_pct"] == 100
    if problem == "gap":
        assert d["risk"]["missing_dates"] == 1


def test_empty_and_single_position_portfolios(session):
    p, _ = _basket(session, count=0)
    assert portfolio_analysis.analyze(session, p, AS_OF)["risk"]["status"] == "empty"
    assert portfolio_analysis.analyze(session, p, AS_OF)["concentration"]["hhi"] is None
    p2, instruments = _basket(session, count=1)
    _history(session, instruments)
    d = portfolio_analysis.analyze(session, p2, AS_OF)
    assert d["concentration"]["hhi"] == 10000 and d["common_funds"]["rows"] == []


def test_different_listing_start_dates_use_shared_history(session):
    p, instruments = _basket(session)
    dates = _history(session, instruments, days=26)
    for day in dates[:3]:
        session.delete(session.get(MarketPrice, (instruments[1].id, day)))
    session.flush()
    risk = portfolio_analysis.analyze(session, p, AS_OF)["risk"]
    assert risk["status"] == "ready" and risk["start"] == dates[3].isoformat()
    assert risk["observations"] == 23 and risk["missing_dates"] == 0


def test_weekly_series_is_not_treated_as_daily_volatility(session):
    p, instruments = _basket(session)
    for instrument in instruments:
        for week in range(23):
            session.add(MarketPrice(instrument_id=instrument.id,
                                    trade_date=AS_OF - timedelta(weeks=week),
                                    close=Decimal(100 + week), source="csv"))
    session.flush()
    risk = portfolio_analysis.analyze(session, p, AS_OF)["risk"]
    assert risk["observations"] == 23 and risk["missing_dates"] == 0
    assert risk["status"] == "incomplete_history"
    assert risk["annualized_volatility_pct"] is None


def test_common_funds_only_latest_books_with_lineage(session, pipeline_run):
    resolver = EntityResolver(session)
    ids = [resolver.instrument(Market.TR, symbol).id for symbol in ("ASELS", "THYAO")]
    result = portfolio_analysis.common_funds(session, "TR", ids, AS_OF)
    assert {row["code"] for row in result["rows"]} == {"IPB", "TI2", "TLY", "TMV"}
    for row in result["rows"]:
        assert row["symbols"] == ["ASELS", "THYAO"]
        assert row["disclosure_id"] and row["confidence"] == Confidence.EXACT
        assert row["as_of"] == "2026-08-31"
    # A newer book with only one of our stocks must not resurrect the older overlapping book.
    fund = session.scalar(select(Fund).where(Fund.code == "TMV"))
    original = next(row for row in result["rows"] if row["code"] == "TMV")
    snapshot = PortfolioSnapshot(fund_id=fund.id, disclosure_id=original["disclosure_id"],
                                 as_of=AS_OF, source="KAP", confidence=Confidence.EXACT)
    session.add(snapshot)
    session.flush()
    session.add(SnapshotHolding(snapshot_id=snapshot.id, instrument_id=ids[0], quantity=100))
    session.flush()
    assert "TMV" not in {r["code"] for r in portfolio_analysis.common_funds(session, "TR", ids, AS_OF)["rows"]}
    assert portfolio_analysis.common_funds(session, "US", ids, AS_OF)["rows"] == []
    assert portfolio_analysis.common_funds(session, "TR", ids, AS_OF + timedelta(days=100))["rows"] == []


@pytest.mark.parametrize("reason", ["superseded", "future", "grouped", "missing_lineage"])
def test_ineligible_disclosures_are_not_used(session, pipeline_run, reason):
    resolver = EntityResolver(session)
    ids = [resolver.instrument(Market.TR, symbol).id for symbol in ("ASELS", "THYAO")]
    # Invalidate all historical books too, so none can legitimately stand in for the latest.
    for snapshot in session.scalars(select(PortfolioSnapshot)):
        disclosure = session.get(Disclosure, snapshot.disclosure_id)
        if reason == "superseded":
            disclosure.is_superseded = True
        elif reason == "future":
            disclosure.published_at = datetime(2026, 12, 1)
        elif reason == "grouped":
            snapshot.confidence = Confidence.GROUPED
        else:
            snapshot.disclosure_id = None
    session.flush()
    assert portfolio_analysis.common_funds(session, "TR", ids, AS_OF)["rows"] == []


def test_analysis_route_enforces_auth_owner_and_plan(session, monkeypatch):
    from instilens.api import deps
    from instilens.api.main import app

    owner = auth.register(session, "analysis@example.com", "password123", "Owner")
    other = auth.register(session, "other-analysis@example.com", "password123", "Other")
    p, _ = _basket(session)
    p.owner_id = str(owner.id)
    session.flush()
    monkeypatch.setattr(settings, "plans_enforced", False)
    app.dependency_overrides[deps.get_session] = lambda: session
    try:
        with TestClient(app) as client:
            path = f"/api/v1/portfolios/{p.id}/analysis"
            assert client.get(path).status_code == 401
            client.headers["authorization"] = f"Bearer {auth.issue_token(other)}"
            assert client.get(path).status_code == 404
            client.headers["authorization"] = f"Bearer {auth.issue_token(owner)}"
            assert client.get(path).status_code == 200
            monkeypatch.setattr(settings, "plans_enforced", True)
            assert client.get(path).status_code == 402
    finally:
        app.dependency_overrides.clear()
