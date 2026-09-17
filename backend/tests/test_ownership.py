"""Ownership read models (/stocks/{symbol}/ownership, /funds/overlap), the crowding engine and its Score rows, and the
PRICE_ABOVE / PRICE_BELOW alert rules. Fixture narrative: five funds report monthly through 2026-08-31; on 08-31 ASELS
is held by all five (9,070,000 lots), THYAO by four, KCHOL by three, EREGL by three and SASA by MAC alone."""

import json
from datetime import date, datetime, timedelta
from decimal import Decimal

from anthropic import beta_tool
from fastapi.testclient import TestClient
from sqlalchemy import select

from instilens.ai.tools import build_tools
from instilens.domain.enums import DisclosureKind, Market, ScoreType, Source
from instilens.domain.models import (
    Fund,
    Institution,
    Instrument,
    MarketPrice,
    Notification,
    PortfolioSnapshot,
    Score,
    SnapshotHolding,
    User,
)
from instilens.domain.schemas import RawDisclosure
from instilens.engine import crowding
from instilens.services import alerts, analytics, auth, ownership, pipeline
from tests.conftest import AS_OF


class _ListAdapter:
    name = "list"

    def __init__(self, items):
        self.items = items

    def fetch(self, since_source_id=None):
        return sorted(self.items, key=lambda r: r.published_at)


def _report(source_id: str, fund: str, as_of: str, holdings: dict[str, int], weights: bool = True, member: str = "GAP") -> RawDisclosure:
    """A KAP portfolio report priced at 10 per share; `weights=False` leaves every weight_pct out, as a report that
    states values only would."""
    total = sum(holdings.values()) * 10
    rows = [{"symbol": s, "quantity": q, "market_value": q * 10, **({"weight_pct": round(q * 10 / total * 100, 4)} if weights else {})} for s, q in holdings.items()]
    return RawDisclosure(
        market=Market.TR, source=Source.KAP, source_id=source_id, kind=DisclosureKind.KAP_PORTFOLIO_REPORT,
        published_at=datetime.fromisoformat(as_of + "T18:00:00"),
        payload={"fund_code": fund, "fund_name": f"{fund} Fonu", "member_oid": f"MOID-{member}", "member_name": f"{member} Portföy", "as_of": as_of, "total_value": total, "holdings": rows},
    )


def _client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    c = TestClient(app)
    token = auth.issue_token(auth.register(session, "o@example.com", "password123", "O"))
    c.headers["authorization"] = f"Bearer {token}"
    return c


def _instrument(session, symbol: str) -> Instrument:
    return session.scalar(select(Instrument).where(Instrument.market_code == "TR", Instrument.symbol == symbol))


# --- holders -----------------------------------------------------------------------------------------------------


def test_holders_come_from_each_funds_latest_snapshot_only(session, pipeline_run):
    """Four monthly reports per fund exist; the holders are the 08-31 rows alone — one per fund, never summed across
    dates (stock_series shows the quantity grew every month, so a double count would be far larger)."""
    asels = _instrument(session, "ASELS")
    assert session.scalar(select(PortfolioSnapshot.fund_id).where(PortfolioSnapshot.as_of == date(2026, 5, 31))) is not None  # older snapshots do exist
    held = ownership.latest_holders(session, asels.id, "TR", AS_OF)
    assert [h.fund for h in held.fresh] == ["IPB", "TMV", "TLY", "TI2", "MAC"] and held.stale == 0  # largest quantity first
    assert all(h.as_of == date(2026, 8, 31) for h in held.fresh)
    assert sum(h.quantity for h in held.fresh) == 9_070_000 == analytics.stock_series(session, "TR", "ASELS")["holdings"][-1]["quantity"]
    assert sum(h.quantity for h in held.fresh) < sum(
        q for (q,) in session.execute(select(SnapshotHolding.quantity).where(SnapshotHolding.instrument_id == asels.id))
    )
    ipb = held.fresh[0]
    assert (ipb.quantity, ipb.market_value, ipb.weight_pct, ipb.confidence) == (3_300_000, Decimal("528000000"), Decimal("70.6922"), "EXACT")
    assert (ipb.last_move, ipb.last_move_period_end) == ("ADD", date(2026, 8, 31))  # the change that produced the 08-31 row
    assert ownership.latest_holders(session, asels.id, "TR", date(2026, 6, 15)).fresh[0].as_of == date(2026, 5, 31)  # as known then
    sasa = ownership.latest_holders(session, _instrument(session, "SASA").id, "TR", AS_OF)
    assert [(h.fund, h.last_move) for h in sasa.fresh] == [("MAC", "REDUCE")]  # the three funds that exited are not holders


def test_stale_snapshots_are_excluded_and_counted(session, pipeline_run):
    asels = _instrument(session, "ASELS")
    assert ownership.staleness_days("TR") == 60 and ownership.staleness_days("US") == 182
    on_edge = ownership.latest_holders(session, asels.id, "TR", date(2026, 10, 30))  # 08-31 is exactly 60 days old: still counted
    assert len(on_edge.fresh) == 5 and on_edge.stale == 0
    too_old = ownership.latest_holders(session, asels.id, "TR", date(2026, 10, 31))
    assert too_old.fresh == [] and too_old.stale == 5
    # A fund whose newest report is from June holds ASELS too: not a holder on 09-14, counted as stale.
    pipeline.run_all(session, _ListAdapter([_report("1701001", "GAP", "2026-06-30", {"ASELS": 1_000_000})]), AS_OF)
    session.commit()
    held = ownership.latest_holders(session, asels.id, "TR", AS_OF)
    assert len(held.fresh) == 5 and held.stale == 1 and "GAP" not in {h.fund for h in held.fresh}
    data = ownership.stock_ownership(session, "TR", "ASELS")
    assert data["stale_holders"] == 1 and data["totals"]["holders"] == 5 and data["totals"]["quantity"] == 9_070_000
    # The market-wide pass (what the pipeline reads) says the same for every instrument in one query.
    by_instrument = ownership.market_holders(session, "TR", AS_OF)
    assert by_instrument[asels.id].stale == 1 and len(by_instrument[asels.id].fresh) == 5
    assert {len(by_instrument[_instrument(session, s).id].fresh) for s in ("THYAO", "KCHOL", "EREGL", "SASA")} == {4, 3, 1}
    assert _instrument(session, "ANELE").id not in by_instrument  # no report holds it


def test_zero_quantity_rows_are_not_holders(session, pipeline_run):
    """A latest snapshot that restates a position at 0 (a 13F line without a share count, a closed position) lists no
    holder for it and puts it in no book: the count, the concentration and the overlap agree with each other."""
    asels, mac = _instrument(session, "ASELS"), session.scalar(select(Fund).where(Fund.code == "MAC"))
    snap = session.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == mac.id).order_by(PortfolioSnapshot.as_of.desc()).limit(1))
    row = session.scalar(select(SnapshotHolding).where(SnapshotHolding.snapshot_id == snap.id, SnapshotHolding.instrument_id == asels.id))
    assert row.quantity > 0 and "MAC" in {h.fund for h in ownership.latest_holders(session, asels.id, "TR", AS_OF).fresh}
    row.quantity = 0
    session.flush()
    held = ownership.latest_holders(session, asels.id, "TR", AS_OF)
    assert [h.fund for h in held.fresh] == ["IPB", "TMV", "TLY", "TI2"] and held.stale == 0
    data = ownership.stock_ownership(session, "TR", "ASELS")
    assert (data["totals"]["holders"], data["totals"]["institutions"], data["totals"]["quantity"]) == (4, 2, 9_070_000 - 520_000)
    assert len(ownership.market_holders(session, "TR", AS_OF)[asels.id].fresh) == 4
    assert "ASELS" not in ownership.fund_books(session, [mac])["MAC"].holdings
    overlap = ownership.fund_overlap(session, ["TMV", "MAC", "IPB"])
    assert [r["symbol"] for r in overlap["common_all"]] == ["EREGL"] and overlap["funds"][1]["holdings"] == 2
    assert "ASELS" in analytics.compare_funds(session, "TMV", "MAC")["only_a"]


def test_ownership_contract_and_pct_of_shares(session, pipeline_run):
    data = ownership.stock_ownership(session, "TR", "ASELS", limit=2)
    assert set(data) == {"symbol", "name", "market", "as_of", "shares_outstanding", "shares_as_of", "currency", "totals", "crowding", "holders", "stale_holders"}
    assert (data["as_of"], data["currency"], data["shares_outstanding"], data["shares_as_of"]) == ("2026-08-31", "TRY", None, None)
    totals = data["totals"]
    assert (totals["holders"], totals["institutions"], totals["quantity"]) == (5, 3, 9_070_000)  # TERA (TMV, TLY), İş (IPB, TI2), Marmara (MAC)
    assert totals["market_value"] == 1_451_200_000.0 and totals["unvalued_holders"] == 0
    assert totals["pct_of_shares"] is None  # no fundamentals yet: never guessed
    assert totals["top10_pct_of_held"] == 100.0 and totals["hhi"] == 2714.75
    assert len(data["holders"]) == 2 and [h["fund"] for h in data["holders"]] == ["IPB", "TMV"]
    holder = data["holders"][0]
    assert set(holder) == {"fund", "name", "institution", "quantity", "market_value", "weight_pct", "pct_of_shares", "as_of", "last_move", "last_move_period_end", "confidence"}
    assert holder["pct_of_shares"] is None and holder["weight_pct"] == 70.6922 and holder["last_move"] == "ADD"
    # Once the fundamentals job has stored the share count, the held share of the company is quantity / shares × 100.
    asels = _instrument(session, "ASELS")
    asels.shares_outstanding, asels.shares_as_of = 100_000_000, date(2026, 9, 10)
    session.flush()
    data = ownership.stock_ownership(session, "TR", "ASELS")
    assert (data["shares_outstanding"], data["shares_as_of"]) == (100_000_000, "2026-09-10")
    assert data["totals"]["pct_of_shares"] == 9.07 and data["holders"][0]["pct_of_shares"] == 3.3 and data["holders"][-1]["pct_of_shares"] == 0.52
    assert ownership.stock_ownership(session, "TR", "NOPE") is None and ownership.stock_ownership(session, "US", "ASELS") is None


def test_concentration_maths():
    top10, hhi = ownership.concentration([50, 30, 20])
    assert (top10, hhi) == (Decimal(100), Decimal(3800))  # 50² + 30² + 20²
    top10, hhi = ownership.concentration([10] * 12)
    assert top10.quantize(Decimal("0.01")) == Decimal("83.33") and hhi.quantize(Decimal("0.01")) == Decimal("833.33")  # 12 × (100/12)²
    top10, hhi = ownership.concentration([1_000_000])
    assert (top10, hhi) == (Decimal(100), Decimal(10000))  # one holder owns everything
    assert ownership.concentration([]) == (None, None) and ownership.concentration([0, 0]) == (None, None)
    assert ownership.pct_of_shares(9_070_000, 100_000_000) == Decimal("9.07")
    assert ownership.pct_of_shares(9_070_000, None) is None and ownership.pct_of_shares(1, 0) is None


# --- overlap -----------------------------------------------------------------------------------------------------


def test_weighted_overlap_helper():
    a = {"ASELS": Decimal("60"), "EREGL": Decimal("10"), "KCHOL": Decimal("30")}
    b = {"ASELS": Decimal("70"), "EREGL": Decimal("5"), "SASA": Decimal("25")}
    assert ownership.weighted_overlap(a, b) == Decimal("65")  # min(60, 70) + min(10, 5)
    assert ownership.symbol_overlap(a, b) == 50.0  # 2 common of 4 symbols
    assert ownership.weighted_overlap(a, {"SASA": Decimal("100")}) == Decimal(0) and ownership.symbol_overlap(a, {"SASA": 1}) == 0.0
    assert ownership.weighted_overlap(a, {**b, "EREGL": None}) is None  # a common holding without a weight: unknown, not a partial sum
    assert ownership.weighted_overlap({}, {}) == Decimal(0) and ownership.symbol_overlap([], []) == 0.0


def test_fund_overlap_contract(session, pipeline_run):
    data = ownership.fund_overlap(session, ["tmv", " MAC", "IPB", "TMV"])  # case-folded, trimmed, de-duplicated in request order
    assert set(data) == {"as_of", "funds", "pairwise", "common_all"} and data["as_of"] == "2026-08-31"
    assert [(f["code"], f["as_of"], f["holdings"]) for f in data["funds"]] == [("TMV", "2026-08-31", 4), ("MAC", "2026-08-31", 3), ("IPB", "2026-08-31", 3)]
    assert data["funds"][0]["institution"].startswith("Tera")
    assert [(p["a"], p["b"]) for p in data["pairwise"]] == [("TMV", "MAC"), ("TMV", "IPB"), ("MAC", "IPB")]
    tmv_mac = data["pairwise"][0]
    assert tmv_mac["overlap_pct_symbols"] == 40.0  # ASELS, EREGL of ASELS, THYAO, EREGL, KCHOL, SASA
    assert tmv_mac["overlap_pct_weighted"] == 72.21  # min(68.7306, 93.1274) + min(3.481, 5.2608)
    assert data["common_all"] == [  # held by all three, the smallest weight anyone gives it first
        {"symbol": "ASELS", "name": "ASELS", "weights": {"TMV": 68.7306, "MAC": 93.1274, "IPB": 70.6922}},
        {"symbol": "EREGL", "name": "EREGL", "weights": {"TMV": 3.481, "MAC": 5.2608, "IPB": 4.4049}},
    ]
    # compare_funds answers the same figures for the pair.
    cmp = analytics.compare_funds(session, "TMV", "MAC")
    assert (cmp["overlap_pct"], cmp["overlap_pct_weighted"]) == (40.0, 72.21) and cmp["common"][0]["a_weight_pct"] == 68.7306
    # A fund that reports no weights: symbol overlap still known, weighted overlap null for its pairs, its weights null in common_all.
    pipeline.run_all(session, _ListAdapter([_report("1701101", "NOW", "2026-08-31", {"ASELS": 100, "THYAO": 50}, weights=False)]), AS_OF)
    session.commit()
    data = ownership.fund_overlap(session, ["TMV", "NOW", "MAC"])
    by_pair = {(p["a"], p["b"]): p for p in data["pairwise"]}
    assert by_pair[("TMV", "NOW")]["overlap_pct_symbols"] == 50.0 and by_pair[("TMV", "NOW")]["overlap_pct_weighted"] is None
    assert by_pair[("TMV", "MAC")]["overlap_pct_weighted"] == 72.21
    assert [r["symbol"] for r in data["common_all"]] == ["ASELS"] and data["common_all"][0]["weights"] == {"TMV": 68.7306, "NOW": None, "MAC": 93.1274}
    assert analytics.compare_funds(session, "NOW", "TMV")["overlap_pct_weighted"] is None
    # No two funds hold nothing in common here; a fund without a snapshot is an empty book with as_of null.
    session.add(Fund(institution_id=session.scalar(select(Institution.id).where(Institution.market_code == "TR")), code="EMPTY", name="Boş Fon"))
    session.flush()
    data = ownership.fund_overlap(session, ["TMV", "EMPTY"])
    assert data["funds"][1]["as_of"] is None and data["funds"][1]["holdings"] == 0 and data["common_all"] == []
    assert data["pairwise"][0]["overlap_pct_symbols"] == 0.0 and data["pairwise"][0]["overlap_pct_weighted"] == 0.0


def test_overlap_route_answers_404_and_422(session, pipeline_run):
    c = _client(session)
    ok = c.get("/api/v1/funds/overlap", params={"codes": "TMV,MAC"})
    assert ok.status_code == 200 and ok.json()["pairwise"][0]["overlap_pct_weighted"] == 72.21  # not swallowed by /funds/{code}
    assert c.get("/api/v1/funds/overlap", params={"codes": "TMV"}).status_code == 422
    assert c.get("/api/v1/funds/overlap", params={"codes": "TMV,TMV"}).status_code == 422  # one fund twice is one fund
    assert c.get("/api/v1/funds/overlap", params={"codes": "TMV,MAC,IPB,TI2,TLY,TMV2,TMV3"}).status_code == 422  # more than six
    assert c.get("/api/v1/funds/overlap", params={"codes": "TMV,NOPE"}).status_code == 404
    assert c.get("/api/v1/funds/overlap").status_code == 422
    us = Institution(market_code="US", code="CIK1", name="US Filer", kind="HEDGE_FUND")
    session.add(us)
    session.flush()
    session.add(Fund(institution_id=us.id, code="CIK1", name="US Filer 13F"))
    session.flush()
    mixed = c.get("/api/v1/funds/overlap", params={"codes": "TMV,CIK1"})
    assert mixed.status_code == 422 and "one market" in mixed.json()["detail"]
    # Pinned to a market (the AI tool passes the assistant's), funds of another market are refused even when they agree.
    assert ownership.fund_overlap(session, ["TMV", "MAC"], "TR")["as_of"] == "2026-08-31"
    try:
        ownership.fund_overlap(session, ["TMV", "MAC"], "US")
    except ownership.OverlapRequestError as exc:
        assert "US market" in str(exc)
    else:
        raise AssertionError("TR funds answered for a US-scoped caller")
    assert c.get("/api/v1/funds/TMV/compare/MAC").json()["overlap_pct_weighted"] == 72.21
    assert c.get("/api/v1/funds/TMV").status_code == 200  # the plain fund route still works
    assert c.get("/api/v1/stocks/ASELS/ownership", params={"limit": 2}).json()["holders"][1]["fund"] == "TMV"
    assert c.get("/api/v1/stocks/ASELS/ownership", params={"market": "US"}).status_code == 404
    assert c.get("/api/v1/stocks/NOPE/ownership").status_code == 404


# --- crowding engine ---------------------------------------------------------------------------------------------


def _inputs(**kw) -> crowding.CrowdingInputs:
    base = {"holders": 0, "pct_of_shares": 0.0, "top10_pct_of_held": None, "hhi": 10000.0, "funds_increasing": 0, "funds_reducing": 0, "window_days": 30}
    return crowding.CrowdingInputs(**{**base, **kw})


def test_crowding_formula_components_and_saturation():
    assert sum(crowding.WEIGHTS.values()) == 1.0
    empty = crowding.crowding_score(_inputs(hhi=None, top10_pct_of_held=None))
    assert (empty.raw, empty.adjusted, empty.level) == (0.0, 0.0, "low") and empty.why["concentration"]["raw"] is None
    full = crowding.crowding_score(_inputs(holders=25, pct_of_shares=30.0, hhi=0.0, funds_increasing=7, funds_reducing=2))
    assert round(full.raw, 6) == 100.0 and full.level == "high" and full.weights == crowding.WEIGHTS
    # Each component alone contributes exactly its weight × 100 at saturation.
    assert crowding.crowding_score(_inputs(holders=25)).raw == 35.0
    assert crowding.crowding_score(_inputs(pct_of_shares=30.0)).raw == 25.0
    assert crowding.crowding_score(_inputs(hhi=0.0)).raw == 20.0
    assert crowding.crowding_score(_inputs(funds_increasing=5)).raw == 20.0
    # Saturation is concave (sqrt) and never exceeds 1; concentration is linear in HHI; momentum is one-sided.
    quarter = crowding.crowding_score(_inputs(holders=6.25))
    assert quarter.components["holders"] == 0.5 and quarter.why["holders"] == {"raw": 6.25, "normalized": 0.5, "weight": 0.35, "contribution": 17.5, "saturation": 25}
    assert crowding.crowding_score(_inputs(holders=100)).components["holders"] == 1.0
    assert crowding.crowding_score(_inputs(hhi=2500.0)).components["concentration"] == 0.75
    assert crowding.crowding_score(_inputs(funds_increasing=2, funds_reducing=5)).components["momentum"] == 0.0
    m = crowding.crowding_score(_inputs(funds_increasing=6, funds_reducing=2, window_days=100)).why["momentum"]
    assert (m["raw"], m["funds_increasing"], m["funds_reducing"], m["window_days"]) == (4, 6, 2, 100) and m["contribution"] == round(100 * 0.2 * (4 / 5) ** 0.5, 2)
    # The breakdown explains every point: contributions add up to the score.
    b = crowding.crowding_score(_inputs(holders=9, pct_of_shares=12.0, hhi=1800.0, funds_increasing=3, funds_reducing=1, top10_pct_of_held=88.0, stale_holders=2))
    assert round(sum(v["contribution"] for k, v in b.why.items() if isinstance(v, dict)), 1) == round(b.raw, 1)
    assert b.why["concentration"]["top10_pct_of_held"] == 88.0 and b.why["stale_holders"] == 2
    js = b.as_json()
    assert set(js) == {"raw", "adjusted", "level", "weights", "components", "why"} and js["raw"] == js["adjusted"] == round(b.raw, 2)


def test_crowding_reweights_when_shares_are_unknown():
    b = crowding.crowding_score(_inputs(holders=25, pct_of_shares=None))
    assert b.why["held_pct"] == {"raw": None, "normalized": None, "weight": 0.0, "contribution": 0.0, "saturation": 30.0, "skipped": crowding.HELD_PCT_SKIPPED}
    assert b.weights["held_pct"] == 0.0 and round(sum(b.weights.values()), 9) == 1.0
    assert round(b.weights["holders"], 4) == round(0.35 / 0.75, 4) == 0.4667 and round(b.raw, 2) == 46.67  # 35 / 0.75
    assert crowding.effective_weights(True) == crowding.WEIGHTS
    known = crowding.crowding_score(_inputs(holders=25, pct_of_shares=0.0))  # a known share count of which funds hold nothing scores 0 there, no reweighting
    assert known.raw == 35.0 and known.why["held_pct"]["weight"] == 0.25 and "skipped" not in known.why["held_pct"]


def test_crowding_levels():
    assert crowding.level_of(0) == "low" and crowding.level_of(34.99) == "low"
    assert crowding.level_of(35) == "medium" and crowding.level_of(50) == "medium" and crowding.level_of(65) == "medium"
    assert crowding.level_of(65.01) == "high" and crowding.level_of(100) == "high"
    assert crowding.crowding_score(_inputs(holders=25, pct_of_shares=30.0)).level == "medium"  # 60
    assert crowding.crowding_score(_inputs(holders=25, pct_of_shares=30.0, hhi=5000.0)).level == "high"  # 70


def test_crowding_rows_are_written_by_compute_intelligence(session, pipeline_run):
    rows = {r.instrument_id: r for r in session.scalars(select(Score).where(Score.score_type == ScoreType.CROWDING, Score.as_of == AS_OF))}
    assert {_instrument(session, s).id for s in ("ASELS", "THYAO", "KCHOL", "EREGL", "SASA")} == set(rows)  # ANELE: no report holds it, no row
    assert all(r.fund_id is None for r in rows.values())
    asels = rows[_instrument(session, "ASELS").id]
    assert float(asels.adjusted_score) == float(asels.raw_score) == 64.15 and asels.components["level"] == "medium"
    why = asels.components["why"]
    assert why["holders"]["raw"] == 5 and why["held_pct"]["weight"] == 0.0 and "skipped" in why["held_pct"]
    assert why["concentration"]["raw"] == 2714.75 and why["concentration"]["top10_pct_of_held"] == 100.0
    activity = analytics.stock_detail(session, "TR", "ASELS")["scores"]["SMART_MONEY"]["why"]["activity"]
    assert (why["momentum"]["funds_increasing"], why["momentum"]["funds_reducing"]) == (activity["funds_increasing"], activity["funds_reducing"]) == (5, 1)
    assert why["momentum"]["window_days"] == 30 and why["stale_holders"] == 0
    assert asels.components["weights"] == {"holders": 0.4667, "held_pct": 0.0, "concentration": 0.2667, "momentum": 0.2667}
    sasa = rows[_instrument(session, "SASA").id]
    assert float(sasa.adjusted_score) == 9.33 and sasa.components["level"] == "low" and sasa.components["why"]["momentum"]["funds_reducing"] == 4
    # stock_detail and the ownership page read the same row.
    detail = analytics.stock_detail(session, "TR", "ASELS")["scores"]["CROWDING"]
    assert detail["score"] == 64.15 and detail["why"]["level"] == "medium" and detail["why"]["why"] == why
    page = ownership.stock_ownership(session, "TR", "ASELS")["crowding"]
    assert page == {"score": 64.15, "level": "medium", "why": why}
    # A second compute of the day replaces the rows (no duplicates); with the share count known the score uses all four weights.
    _instrument(session, "ASELS").shares_outstanding = 100_000_000
    session.flush()
    pipeline.compute_intelligence(session, AS_OF)
    session.commit()
    again = session.scalars(select(Score).where(Score.score_type == ScoreType.CROWDING, Score.as_of == AS_OF)).all()
    assert len(again) == 5
    (asels,) = [r for r in again if r.instrument_id == _instrument(session, "ASELS").id]
    assert asels.components["why"]["held_pct"]["raw"] == 9.07 and asels.components["weights"] == {"holders": 0.35, "held_pct": 0.25, "concentration": 0.2, "momentum": 0.2}
    assert float(asels.adjusted_score) == round(100 * (0.35 * (5 / 25) ** 0.5 + 0.25 * (9.07 / 30) ** 0.5 + 0.2 * (1 - 0.271475) + 0.2 * (4 / 5) ** 0.5), 2)
    # Yesterday's compute knew no 08-31 report: the holders then were the 07-31 books, and momentum was zero.
    pipeline.compute_intelligence(session, date(2026, 8, 25))
    session.commit()
    past = session.scalar(select(Score).where(Score.score_type == ScoreType.CROWDING, Score.as_of == date(2026, 8, 25), Score.instrument_id == _instrument(session, "SASA").id))
    assert past.components["why"]["holders"]["raw"] == 4  # TMV, TLY, IPB, MAC still held SASA on 07-31


# --- price alerts ------------------------------------------------------------------------------------------------


def _close(session, symbol: str, on: date, close: str) -> None:
    session.add(MarketPrice(instrument_id=_instrument(session, symbol).id, trade_date=on, close=Decimal(close), source="csv"))
    session.flush()


def test_price_alerts_fire_once_per_crossing(session, pipeline_run):
    """ASELS closes: 08-31 160, 09-14 140. A PRICE_BELOW 150 rule sees the crossing on 09-14; PRICE_ABOVE 130 does
    not fire (already above at the previous close); later closes re-cross and each crossing notifies once."""
    c = _client(session)
    assert {"PRICE_ABOVE", "PRICE_BELOW"} <= alerts.RULE_TYPES and set(alerts.PRICE_RULES).isdisjoint(alerts.WATCHLIST_STOCK_RULES)
    assert {"PRICE_ABOVE", "PRICE_BELOW"} <= set(c.get("/api/v1/alerts/rules").json()["rule_types"])
    below = c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "PRICE_BELOW", "params": {"price": 150}})
    assert below.status_code == 201
    assert c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "PRICE_ABOVE", "params": {"price": 130}}).status_code == 201
    above = c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "PRICE_ABOVE", "params": {"price": "150.50"}})
    assert above.status_code == 201
    assert c.post("/api/v1/watchlist", json={"symbol": "ASELS"}).status_code == 201  # watching adds no implicit price rule
    assert alerts.evaluate(session, AS_OF) >= 1
    price_notes = [n for n in session.scalars(select(Notification)) if n.alert_rule_id in (below.json()["id"], above.json()["id"])]
    assert len(price_notes) == 1 and price_notes[0].dedup_key == f"{below.json()['id']}:2026-09-14"
    assert price_notes[0].title == "ASELS: kapanış 140 ₺ ile eşik 150 ₺ altında" and price_notes[0].body == "kapanış tarihi 2026-09-14 · önceki kapanış 160 ₺"
    assert price_notes[0].link == "/stocks/ASELS" and not any(n.title.startswith("ASELS: kapanış") and "130" in n.title for n in session.scalars(select(Notification)))
    assert alerts.evaluate(session, AS_OF) == 0  # the same close date never notifies twice
    # 09-15 closes at 155.5: above 150.5 for the first time → one notification, keyed by that date; 09-16 stays above → nothing.
    _close(session, "ASELS", date(2026, 9, 15), "155.5")
    assert alerts.evaluate(session, date(2026, 9, 15)) == 1
    (note,) = [n for n in session.scalars(select(Notification)) if n.alert_rule_id == above.json()["id"]]
    assert note.title == "ASELS: kapanış 155,5 ₺ ile eşik 150,5 ₺ üzerinde" and note.dedup_key == f"{above.json()['id']}:2026-09-15" and "önceki kapanış 140 ₺" in note.body
    _close(session, "ASELS", date(2026, 9, 16), "158")
    assert alerts.evaluate(session, date(2026, 9, 16)) == 0
    # Dips below on 09-17 (PRICE_BELOW 150 fires again — a new crossing, a new date) and re-crosses 150.5 on 09-18.
    _close(session, "ASELS", date(2026, 9, 17), "145")
    assert alerts.evaluate(session, date(2026, 9, 17)) == 1
    _close(session, "ASELS", date(2026, 9, 18), "152")
    assert alerts.evaluate(session, date(2026, 9, 18)) == 1
    keys = sorted(n.dedup_key for n in session.scalars(select(Notification)) if n.alert_rule_id in (below.json()["id"], above.json()["id"]))
    assert keys == sorted([f"{below.json()['id']}:2026-09-14", f"{below.json()['id']}:2026-09-17", f"{above.json()['id']}:2026-09-15", f"{above.json()['id']}:2026-09-18"])
    assert alerts.evaluate(session, date(2026, 9, 18)) == 0
    # Evaluated as of a day before any close: nothing to compare, nothing fires.
    assert alerts.evaluate(session, date(2026, 5, 1)) == 0


def test_price_alert_english_text_and_first_close(session, pipeline_run):
    from instilens.domain.models import AlertRule

    session.add(User(email="en@example.com", password_hash="x", name="E", lang="en"))
    session.flush()
    owner = str(session.scalar(select(User.id).where(User.email == "en@example.com")))
    thyao = _instrument(session, "THYAO")  # closes: 08-31 310, 09-14 315
    session.add(AlertRule(owner_id=owner, instrument_id=thyao.id, rule_type="PRICE_ABOVE", params={"price": 312.25}))
    session.flush()
    assert alerts.evaluate(session, AS_OF) == 1
    (note,) = session.scalars(select(Notification).where(Notification.owner_id == owner)).all()
    assert note.title == "THYAO: close 315 ₺ is above the 312.25 ₺ threshold" and note.body == "close date 2026-09-14 · previous close 310 ₺"
    assert "buy" not in note.title.lower() and "sell" not in note.title.lower()
    # A stock with a single close: beyond the threshold means crossed (there is no earlier close to compare with).
    kchol = _instrument(session, "KCHOL")
    session.execute(MarketPrice.__table__.delete().where(MarketPrice.instrument_id == kchol.id))
    _close(session, "KCHOL", AS_OF, "99")
    session.add(AlertRule(owner_id=owner, instrument_id=kchol.id, rule_type="PRICE_BELOW", params={"price": 100}))
    session.flush()
    assert alerts.evaluate(session, AS_OF) == 1
    first = session.scalar(select(Notification).where(Notification.owner_id == owner, Notification.title.like("KCHOL%")))
    assert first.title == "KCHOL: close 99 ₺ is below the 100 ₺ threshold" and first.body.endswith("previous close none")


def test_price_rule_params_are_validated(session, pipeline_run):
    c = _client(session)
    for params in ({}, {"price": 0}, {"price": -5}, {"price": "abc"}, {"price": True}, {"price": None}, {"price": "inf"}, {"price": [1]}):
        r = c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "PRICE_ABOVE", "params": params})
        assert r.status_code == 400 and "price" in r.json()["detail"], params
    r = c.post("/api/v1/alerts/rules", json={"fund_code": "TMV", "rule_type": "PRICE_BELOW", "params": {"price": 10}})
    assert r.status_code == 400 and "symbol" in r.json()["detail"]  # stocks only: funds have no close
    assert c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "PRICE_BELOW", "params": {"price": "123.4"}}).status_code == 201
    assert c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "PRICE_ABOVE", "params": {"price": 200, "note": "kept"}}).status_code == 201
    for params in ({"price": 1e10}, {"price": "1e300"}, {"price": 1e-300}, {"price": 0.0000001}):  # above the cap, or rounding to 0 at six decimals
        r = c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "PRICE_ABOVE", "params": params})
        assert r.status_code == 400 and "price" in r.json()["detail"], params
    assert c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "rule_type": "PRICE_ABOVE", "params": {"price": 1e9}}).status_code == 201
    listed = c.get("/api/v1/alerts/rules").json()["rules"]  # newest first
    rules = {r["params"]["price"]: r for r in listed}
    # `since` is the latest close known at creation (ASELS: 09-14); the list carries the subject's market.
    assert rules[123.4]["params"] == {"price": 123.4, "since": "2026-09-14"} and rules[200.0]["params"] == {"price": 200.0, "note": "kept", "since": "2026-09-14"}
    assert listed[0]["params"] == {"price": 1e9, "since": "2026-09-14"}
    assert rules[123.4]["symbol"] == "ASELS" and rules[123.4]["market"] == "TR" and rules[123.4]["rule_type"] == "PRICE_BELOW"
    assert c.post("/api/v1/alerts/rules", json={"fund_code": "TMV", "rule_type": "FUND_ACTIVITY"}).status_code == 201
    fund_rule = c.get("/api/v1/alerts/rules").json()["rules"][0]
    assert (fund_rule["fund_code"], fund_rule["symbol"], fund_rule["market"]) == ("TMV", None, "TR")
    # A stock with no close yet starts today: a history backfill cannot fire the rule for crossings before it existed.
    anele = _instrument(session, "ANELE")
    session.execute(MarketPrice.__table__.delete().where(MarketPrice.instrument_id == anele.id))
    session.flush()
    assert c.post("/api/v1/alerts/rules", json={"symbol": "ANELE", "rule_type": "PRICE_ABOVE", "params": {"price": 10}}).status_code == 201
    assert c.get("/api/v1/alerts/rules").json()["rules"][0]["params"]["since"] == date.today().isoformat()


def test_price_alert_survives_missed_evaluates(session, pipeline_run):
    """Two closes land between two evaluates (a lagging feed, a skipped compute): the crossing still fires, on its own
    date, and once. KCHOL closes 100 (09-10), 100 (09-11), 110 (09-14), 120 (09-15); PRICE_ABOVE 105 evaluated on
    09-11 and then only on 09-15."""
    from instilens.domain.models import AlertRule

    owner = str(auth.register(session, "p@example.com", "password123", "P").id)
    kchol = _instrument(session, "KCHOL")
    session.execute(MarketPrice.__table__.delete().where(MarketPrice.instrument_id == kchol.id))
    for on, close in ((date(2026, 9, 10), "100"), (date(2026, 9, 11), "100")):
        _close(session, "KCHOL", on, close)
    rule = AlertRule(owner_id=owner, instrument_id=kchol.id, rule_type="PRICE_ABOVE", params={"price": 105, "since": "2026-09-11"})
    session.add(rule)
    session.flush()
    assert alerts.evaluate(session, date(2026, 9, 11)) == 0
    _close(session, "KCHOL", date(2026, 9, 14), "110")
    _close(session, "KCHOL", date(2026, 9, 15), "120")
    assert alerts.evaluate(session, date(2026, 9, 15)) == 1
    (note,) = session.scalars(select(Notification).where(Notification.alert_rule_id == rule.id)).all()
    assert note.dedup_key == f"{rule.id}:2026-09-14" and "önceki kapanış 100 ₺" in note.body and "kapanış 110 ₺" in note.title
    assert alerts.evaluate(session, date(2026, 9, 15)) == 0
    # Dips back on 09-16, re-crosses on 09-17 and 09-18 is missed again: evaluated on 09-18 alone, only 09-17 fires.
    _close(session, "KCHOL", date(2026, 9, 16), "104")
    _close(session, "KCHOL", date(2026, 9, 17), "106")
    _close(session, "KCHOL", date(2026, 9, 18), "108")
    assert alerts.evaluate(session, date(2026, 9, 18)) == 1
    assert sorted(n.dedup_key for n in session.scalars(select(Notification).where(Notification.alert_rule_id == rule.id))) == [f"{rule.id}:2026-09-14", f"{rule.id}:2026-09-17"]


def test_price_rule_created_after_the_crossing_waits(session, pipeline_run):
    """KCHOL closes 100 (09-10), 140 (09-11), 141 (09-14). A PRICE_ABOVE 120 rule created on 09-15 (`since` = the
    latest close then, 09-14) never fires for the 09-11 crossing; it fires on the next one. A rule whose latest close
    at creation is itself the crossing does fire for it (`since` = that date)."""
    from instilens.domain.models import AlertRule

    owner = str(auth.register(session, "p@example.com", "password123", "P").id)
    kchol = _instrument(session, "KCHOL")
    session.execute(MarketPrice.__table__.delete().where(MarketPrice.instrument_id == kchol.id))
    for on, close in ((date(2026, 9, 10), "100"), (date(2026, 9, 11), "140"), (date(2026, 9, 14), "141")):
        _close(session, "KCHOL", on, close)
    late = AlertRule(owner_id=owner, instrument_id=kchol.id, rule_type="PRICE_ABOVE", params={"price": 120, "since": "2026-09-14"})
    session.add(late)
    session.flush()
    assert alerts.evaluate(session, date(2026, 9, 15)) == 0
    _close(session, "KCHOL", date(2026, 9, 15), "100")
    _close(session, "KCHOL", date(2026, 9, 16), "150")
    assert alerts.evaluate(session, date(2026, 9, 16)) == 1
    (note,) = session.scalars(select(Notification).where(Notification.alert_rule_id == late.id)).all()
    assert note.dedup_key == f"{late.id}:2026-09-16"
    on_the_day = AlertRule(owner_id=owner, instrument_id=kchol.id, rule_type="PRICE_ABOVE", params={"price": 145, "since": "2026-09-16"})
    session.add(on_the_day)
    session.flush()
    assert alerts.evaluate(session, date(2026, 9, 16)) == 1
    assert session.scalar(select(Notification.dedup_key).where(Notification.alert_rule_id == on_the_day.id)) == f"{on_the_day.id}:2026-09-16"


def test_price_alert_window_edge_is_not_a_crossing(session, pipeline_run):
    """More closes than the walk covers, all beyond the threshold: the oldest close in the window is only the
    "previous" of the next one, so nothing fires on any day — the crossing was before the window, not at its edge."""
    from instilens.domain.models import AlertRule

    owner = str(auth.register(session, "p@example.com", "password123", "P").id)
    kchol = _instrument(session, "KCHOL")
    session.execute(MarketPrice.__table__.delete().where(MarketPrice.instrument_id == kchol.id))
    days = [date(2026, 8, 1) + timedelta(days=i) for i in range(alerts.PRICE_LOOKBACK + 5)]
    _close(session, "KCHOL", days[0], "90")
    for on in days[1:]:
        _close(session, "KCHOL", on, "110")
    rule = AlertRule(owner_id=owner, instrument_id=kchol.id, rule_type="PRICE_ABOVE", params={"price": 100})
    session.add(rule)
    session.flush()
    assert alerts.evaluate(session, days[1]) == 1  # the real crossing, on its date
    for on in days[2:]:
        assert alerts.evaluate(session, on) == 0, on
    assert alerts.evaluate(session, days[-1] + timedelta(days=30)) == 0


# --- AI tools ----------------------------------------------------------------------------------------------------


def test_ownership_tools_return_json(session, pipeline_run):
    tools = {fn.__name__: beta_tool(fn) for fn in build_tools(session)}
    own = json.loads(tools["get_stock_ownership"].call({"symbol": "asels", "limit": 2}))
    assert own["symbol"] == "ASELS" and own["totals"]["holders"] == 5 and [h["fund"] for h in own["holders"]] == ["IPB", "TMV"]
    assert own["crowding"]["level"] == "medium" and own["stale_holders"] == 0
    assert "error" in json.loads(tools["get_stock_ownership"].call({"symbol": "NOPE"}))
    overlap = json.loads(tools["get_fund_overlap"].call({"codes": ["TMV", "MAC"]}))
    assert overlap["pairwise"] == [{"a": "TMV", "b": "MAC", "overlap_pct_symbols": 40.0, "overlap_pct_weighted": 72.21}]
    assert "error" in json.loads(tools["get_fund_overlap"].call({"codes": ["TMV"]})) and "error" in json.loads(tools["get_fund_overlap"].call({"codes": ["TMV", "NOPE"]}))
    us_tools = {fn.__name__: beta_tool(fn) for fn in build_tools(session, "US")}
    assert "US market" in json.loads(us_tools["get_fund_overlap"].call({"codes": ["TMV", "MAC"]}))["error"]  # TR funds, US-scoped assistant
    assert tools["get_fund_overlap"].to_dict()["input_schema"]["properties"]["codes"]["type"] == "array"
    score = json.loads(tools["get_crowding_score"].call({"symbol": "ASELS"}))
    assert set(score) == {"symbol", "name", "market", "as_of", "shares_outstanding", "totals", "crowding", "stale_holders"}
    assert score["crowding"]["score"] == 64.15 and score["crowding"]["why"]["holders"]["raw"] == 5 and score["totals"]["hhi"] == 2714.75
    assert "error" in json.loads(tools["get_crowding_score"].call({"symbol": "NOPE"}))
    for name in ("get_stock_ownership", "get_fund_overlap", "get_crowding_score"):
        desc = tools[name].to_dict()["description"]
        assert "not recommendations" in desc or "not a valuation view" in desc
    from instilens.ai.prompts import SYSTEM_PROMPT

    assert "get_crowding_score" in SYSTEM_PROMPT and "not a valuation view" in SYSTEM_PROMPT
