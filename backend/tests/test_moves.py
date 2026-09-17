"""Position engine v2 (values, weight delta, gap periods) and the moves read model behind /moves and the AI tools."""

from datetime import date, datetime
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from instilens.domain.enums import ActivityType, DisclosureKind, Market, Source
from instilens.domain.models import Fund, PositionChange
from instilens.domain.schemas import RawDisclosure
from instilens.engine.positions import HoldingView, SnapshotView, diff_snapshots
from instilens.services import analytics, pipeline
from tests.conftest import AS_OF, FIXTURES


class _ListAdapter:
    name = "list"

    def __init__(self, items):
        self.items = items

    def fetch(self, since_source_id=None):
        return sorted(self.items, key=lambda r: r.published_at)


def _report(source_id: str, fund: str, as_of: str, holdings: dict[str, int]) -> RawDisclosure:
    """A KAP portfolio report with values priced at 10 per share, so weights are known and sum to 100."""
    total = sum(holdings.values()) * 10
    return RawDisclosure(
        market=Market.TR, source=Source.KAP, source_id=source_id, kind=DisclosureKind.KAP_PORTFOLIO_REPORT,
        published_at=datetime.fromisoformat(as_of + "T18:00:00"),
        payload={"fund_code": fund, "fund_name": f"{fund} Fonu", "member_oid": "MOID-GAP", "member_name": "Gap Portföy", "as_of": as_of, "total_value": total,
                 "holdings": [{"symbol": s, "quantity": q, "market_value": q * 10, "weight_pct": round(q * 10 / total * 100, 4)} for s, q in holdings.items()]},
    )


def _event(source_id: str, symbol: str, member: str, funds: list[str], on: str, rows: list[tuple[str, int]], before, after) -> RawDisclosure:
    """A KAP share-transaction disclosure; every row priced at 100, so the value is nominal × 100."""
    return RawDisclosure(
        market=Market.TR, source=Source.KAP, source_id=source_id, kind=DisclosureKind.KAP_SHARE_TRANSACTION,
        published_at=datetime.fromisoformat(on + "T18:00:00"),
        payload={"member_oid": f"MOID-{member}", "member_name": f"{member} Portföy", "subject_symbol": symbol, "related_fund_codes": funds,
                 "rows": [{"transaction_date": on, "side": side, "nominal": nominal, "price": 100} for side, nominal in rows],
                 "ownership_before_pct": before, "ownership_after_pct": after},
    )


def test_diff_carries_values_weight_delta_and_relative_change():
    prev = SnapshotView("TMV", date(2026, 7, 31), (
        HoldingView("ASELS", 100, market_value=Decimal("15000"), weight_pct=Decimal("5")),   # reported value wins over the price map
        HoldingView("EREGL", 80, weight_pct=Decimal("3")),                                   # no reported value → qty × close
        HoldingView("SASA", 50, market_value=Decimal("500"), weight_pct=Decimal("2")),
        HoldingView("KCHOL", 10, weight_pct=None),
    ))
    curr = SnapshotView("TMV", date(2026, 8, 31), (
        HoldingView("ASELS", 150, market_value=Decimal("24000"), weight_pct=Decimal("7")),
        HoldingView("EREGL", 40, weight_pct=Decimal("1.5")),
        HoldingView("THYAO", 20, weight_pct=Decimal("2")),
        HoldingView("KCHOL", 10, weight_pct=Decimal("1")),
    ))
    prices, from_prices = {"ASELS": Decimal("140"), "EREGL": Decimal("50")}, {"ASELS": Decimal("120"), "EREGL": Decimal("40")}
    by = {d.instrument_symbol: d for d in diff_snapshots(prev, curr, prices=prices, from_prices=from_prices)}
    asels = by["ASELS"]
    assert (asels.from_value, asels.to_value) == (Decimal("15000"), Decimal("24000"))
    assert asels.delta_weight_pct == Decimal("2") and asels.pct_change_qty == Decimal("50.0000")
    eregl = by["EREGL"]
    assert (eregl.from_value, eregl.to_value) == (Decimal("3200"), Decimal("2000"))  # 80 × close at period_start (40), 40 × close at period_end (50)
    assert eregl.delta_value == Decimal("-2000")  # the flow itself stays priced at period_end
    assert eregl.delta_weight_pct == Decimal("-1.5") and eregl.pct_change_qty == Decimal("-50.0000")
    fallback = {d.instrument_symbol: d for d in diff_snapshots(prev, curr, prices=prices)}["EREGL"]
    assert fallback.from_value == Decimal("4000")  # without period_start closes the from-side is marked at period_end
    sasa = by["SASA"]  # full exit: the position is worth 0 afterwards, the weight afterwards is unknown
    assert sasa.activity is ActivityType.EXIT and sasa.to_value == Decimal(0) and sasa.delta_weight_pct is None
    assert sasa.pct_change_qty == Decimal("-100.0000")
    thyao = by["THYAO"]  # new position: starts from 0, no previous weight, no relative change
    assert thyao.activity is ActivityType.NEW and thyao.from_value == Decimal(0) and thyao.to_value is None  # no value, no price
    assert thyao.delta_weight_pct is None and thyao.pct_change_qty is None
    kchol = by["KCHOL"]  # HOLD rows are still produced; the unknown previous weight leaves the delta unknown
    assert kchol.activity is ActivityType.HOLD and kchol.pct_change_qty == Decimal("0.0000") and kchol.delta_weight_pct is None


def test_pipeline_persists_v2_fields_and_consecutive_periods(session, pipeline_run):
    rows = session.scalars(select(PositionChange)).all()
    assert rows and {r.gap_periods for r in rows} == {1}  # monthly fixture chain, no report missed
    tmv = session.scalar(select(Fund).where(Fund.code == "TMV"))
    by_period = {(c.period_end, _symbol(session, c.instrument_id)): c for c in rows if c.fund_id == tmv.id}
    asels = by_period[(date(2026, 8, 31), "ASELS")]
    assert asels.from_value == Decimal("324000") * 1000 and asels.to_value is not None  # reported market values
    assert asels.pct_change_qty == Decimal("45") and asels.delta_weight_pct == asels.to_weight_pct - asels.from_weight_pct
    thyao = by_period[(date(2026, 8, 31), "THYAO")]
    assert thyao.activity == ActivityType.NEW and thyao.from_value == 0 and thyao.pct_change_qty is None
    detail = analytics.fund_detail(session, "TMV")
    change = detail["activity"][ActivityType.NEW][0]
    assert change["symbol"] == "THYAO" and change["gap_periods"] == 1 and change["from_value"] == 0.0
    assert {"to_value", "delta_weight_pct", "pct_change_qty"} <= set(change)


def test_gap_periods_counts_missed_monthly_reports(session):
    reports = [_report("1701001", "GAP", "2026-06-30", {"ASELS": 100}), _report("1701002", "GAP", "2026-08-31", {"ASELS": 150}),
               _report("1701003", "GAP", "2026-09-30", {"ASELS": 150, "THYAO": 10})]
    out = pipeline.run_all(session, _ListAdapter(reports), date(2026, 10, 1))
    assert out["parsed"] == 3
    by_end = {c.period_end: c.gap_periods for c in session.scalars(select(PositionChange))}
    assert by_end == {date(2026, 8, 31): 2, date(2026, 9, 30): 1}  # June → August skipped July; August → September is consecutive


def test_gap_periods_counts_missed_quarterly_filings(session):
    import json

    q4, q2 = (RawDisclosure.model_validate(json.loads((FIXTURES / "sec" / f"{acc}.json").read_text(encoding="utf-8")))
              for acc in ("0001193125-26-054580", "0001193125-26-352200"))  # 2025-12-31 and 2026-06-30: Q1 is missing
    out = pipeline.run_all(session, _ListAdapter([q4, q2]), date(2026, 9, 14), market=Market.US)
    assert out["position_changes"] > 0
    assert {c.gap_periods for c in session.scalars(select(PositionChange))} == {2}


def test_market_moves_follow_window_flows_and_name_the_parties(session, pipeline_run):
    flows = analytics.window_flows(session, "TR", 30, AS_OF)
    buys = analytics.moves(session, "TR", kind="buys")
    assert (buys["as_of"], buys["window_days"], buys["window_start"], buys["kind"], buys["market"], buys["fund"]) == (AS_OF.isoformat(), 30, "2026-08-15", "buys", "TR", None)
    assert [(r["symbol"], r["net_flow_value"]) for r in buys["rows"]] == [(r["symbol"], r["net_flow_value"]) for r in flows["accumulated"]]
    thyao = buys["rows"][0]
    assert thyao["symbol"] == "THYAO" and (thyao["funds_increasing"], thyao["funds_new"], thyao["party_count"]) == (4, 3, 4)
    assert [p["code"] for p in thyao["parties"]] == ["TMV", "IPB", "TLY", "TI2"]  # largest |delta_value| first
    tmv = thyao["parties"][0]  # 08-31 snapshot entry + the uncovered EXACT buy of 09-11 (1608402) fold into one party
    assert tmv["kind"] == "fund" and tmv["name"].startswith("Tera") and tmv["activity"] == "NEW"
    assert tmv["delta_qty"] == 400_000 + 1_200_000 and tmv["period_end"] == "2026-09-11" and tmv["confidence"] == "INFERRED"
    assert tmv["to_weight_pct"] > 0 and tmv["delta_weight_pct"] is None
    assert {k for p in thyao["parties"] for k in p} == {"kind", "code", "name", "activity", "delta_qty", "delta_value", "to_weight_pct", "delta_weight_pct", "period_end", "confidence"}
    anele = next(r for r in buys["rows"] if r["symbol"] == "ANELE")  # one GROUPED entry = one institution party
    (party,) = anele["parties"]
    assert party["kind"] == "institution" and party["confidence"] == "GROUPED" and party["activity"] == "NEW" and anele["party_count"] == 1
    asels = next(r for r in buys["rows"] if r["symbol"] == "ASELS")  # 5 funds added, the grouped 09-12 sell is the sixth party
    assert asels["party_count"] == 6 and len(asels["parties"]) == 5 and asels["funds_reducing"] == 1

    sells = analytics.moves(session, "TR", kind="sells")
    assert [r["symbol"] for r in sells["rows"]] == [r["symbol"] for r in flows["distributed"]] == ["SASA", "EREGL"]
    new = analytics.moves(session, "TR", kind="new")
    assert [(r["symbol"], r["funds_new"]) for r in new["rows"]] == [("THYAO", 3), ("ANELE", 1)]
    assert all(p["activity"] == "NEW" for r in new["rows"] for p in r["parties"]) and new["rows"][0]["party_count"] == 3
    exits = analytics.moves(session, "TR", kind="exits")
    assert [(r["symbol"], r["funds_exited"], r["party_count"]) for r in exits["rows"]] == [("SASA", 3, 3)]
    assert sorted(p["code"] for p in exits["rows"][0]["parties"]) == ["IPB", "TLY", "TMV"]
    limited = analytics.moves(session, "TR", kind="buys", limit=1)
    assert limited["rows"] == buys["rows"][:1] and limited["total"] == buys["total"] == len(buys["rows"]) > 1  # total counts before the cut
    assert analytics.moves(session, "TR", kind="buys", window_days=7)["rows"][0]["symbol"] == "ANELE"  # only post-snapshot events in 7D


def test_fund_moves_have_the_fund_as_their_single_party(session, pipeline_run):
    buys = analytics.moves(session, "TR", kind="buys", fund_code="tmv")
    assert buys["fund"] == {"code": "TMV", "name": "Tera Portföy Algoritmik Stratejiler Serbest Fon"}
    assert [(r["symbol"], r["parties"][0]["activity"]) for r in buys["rows"]] == [("THYAO", "NEW"), ("ASELS", "ADD")]
    assert all(r["party_count"] == 1 and r["parties"][0]["code"] == "TMV" for r in buys["rows"])
    assert buys["rows"][0]["net_qty"] == 1_600_000 and buys["rows"][0]["funds_new"] == 1  # snapshot entry + its EXACT buy
    sells = analytics.moves(session, "TR", kind="sells", fund_code="TMV")
    assert [(r["symbol"], r["parties"][0]["activity"]) for r in sells["rows"]] == [("SASA", "EXIT"), ("EREGL", "REDUCE")]
    assert [r["symbol"] for r in analytics.moves(session, "TR", kind="new", fund_code="TMV")["rows"]] == ["THYAO"]
    assert [r["symbol"] for r in analytics.moves(session, "TR", kind="exits", fund_code="TMV")["rows"]] == ["SASA"]
    ipb = analytics.moves(session, "TR", kind="sells", fund_code="IPB")  # the GROUPED 09-12 ASELS sell (IPB + TI2) is not IPB's own move
    assert [r["symbol"] for r in ipb["rows"]] == ["SASA", "EREGL"]
    assert analytics.moves(session, "TR", kind="buys", fund_code="NOPE") is None
    assert analytics.moves(session, "US", kind="buys", fund_code="TMV") is None  # a TR fund is unknown in the US market


def test_moves_never_list_a_row_without_parties_and_ignore_zero_net_events(session, pipeline_run):
    """Two edge cases the score engine already handles: a party that entered and left again inside the window folds
    to one EXIT party (so the stock is an exit, not a new position), and a MIXED event that nets to zero moves nobody."""
    events = [
        _event("1609101", "TUPRS", "TERA", ["TMV"], "2026-09-08", [("ALIS", 100_000)], 0, "0.40"),      # first-time entry ...
        _event("1609102", "TUPRS", "TERA", ["TMV"], "2026-09-10", [("SATIS", 100_000)], "0.40", 0),    # ... fully sold two days later
        _event("1609103", "THYAO", "IS", ["IPB", "TI2"], "2026-09-12", [("ALIS", 50_000), ("SATIS", 50_000)], "0.50", "0.50"),  # round trip
    ]
    pipeline.run_all(session, _ListAdapter(events), AS_OF)
    exits = analytics.moves(session, "TR", kind="exits")
    tuprs = next(r for r in exits["rows"] if r["symbol"] == "TUPRS")
    assert (tuprs["funds_new"], tuprs["funds_exited"], tuprs["party_count"]) == (1, 1, 1) and tuprs["parties"][0]["activity"] == "EXIT"
    assert tuprs["net_qty"] == 0 and tuprs["net_flow_value"] == 0.0
    assert "TUPRS" not in [r["symbol"] for r in analytics.moves(session, "TR", kind="new")["rows"]]  # its only entrant is gone
    flows = analytics.window_flows(session, "TR", 30, AS_OF)
    assert "TUPRS" not in [r["symbol"] for r in flows["accumulated"] + flows["distributed"]]  # a Decimal zero is neither side
    thyao = next(r for r in analytics.moves(session, "TR", kind="buys")["rows"] if r["symbol"] == "THYAO")
    assert (thyao["funds_increasing"], thyao["funds_reducing"], thyao["party_count"]) == (4, 0, 4)  # the round trip adds no reducer
    assert all(p["kind"] == "fund" for p in thyao["parties"])


def test_moves_load_event_funds_in_one_query(session, pipeline_run):
    """The de-dup law reads every event's funds; they come with the events, not one SELECT per event."""
    from sqlalchemy import event
    from sqlalchemy.orm import sessionmaker

    fresh = sessionmaker(bind=session.get_bind())()  # the pipeline's own session still holds the funds it wrote
    statements: list[str] = []
    listener = lambda conn, cursor, statement, *_: statements.append(statement)  # noqa: E731
    event.listen(fresh.get_bind(), "before_cursor_execute", listener)
    try:
        buys = analytics.moves(fresh, "TR", kind="buys")
    finally:
        event.remove(fresh.get_bind(), "before_cursor_execute", listener)
        fresh.close()
    assert buys["rows"][0]["party_count"] == 4  # the funds were read
    assert sum("transaction_event_funds" in q for q in statements) == 1
    assert sum("FROM instruments" in q and "instruments.id IN" in q for q in statements) == 1  # only the instruments that moved


def test_moves_route(session, pipeline_run):
    import pytest

    from instilens.api import deps
    from instilens.api.main import app
    from instilens.services import auth

    app.dependency_overrides[deps.get_session] = lambda: session
    client = TestClient(app)
    assert client.get("/api/v1/moves?kind=buys").status_code == 401
    client.headers["authorization"] = f"Bearer {auth.issue_token(auth.register(session, 'm@example.com', 'password123', 'M'))}"
    body = client.get("/api/v1/moves?kind=buys&market=TR").json()
    assert body["kind"] == "buys" and body["rows"][0]["symbol"] == "THYAO" and body["fund"] is None
    body = client.get("/api/v1/moves?kind=exits&fund=tmv&window=60&limit=5").json()
    assert body["fund"]["code"] == "TMV" and body["window_days"] == 60 and [r["symbol"] for r in body["rows"]] == ["SASA"]
    assert client.get("/api/v1/moves?kind=buys&fund=NOPE").status_code == 404
    for bad in ("kind=holds", "kind=buys&window=0", "kind=buys&window=731", "kind=buys&limit=101", "kind=buys&market=DE", ""):
        assert client.get(f"/api/v1/moves?{bad}").status_code == 422, bad
    with pytest.raises(ValueError):
        analytics.moves(session, "TR", kind="holds")
    app.dependency_overrides.clear()


# The /moves wire contract the frontend (src/lib/api.ts: Moves / MoveRow / MoveParty) is typed against.
MOVES_KEYS = {"as_of", "window_days", "window_start", "kind", "market", "fund", "total", "rows"}
MOVE_ROW_KEYS = {"symbol", "name", "net_flow_value", "net_qty", "funds_increasing", "funds_reducing", "funds_new", "funds_exited", "party_count", "parties"}
MOVE_PARTY_KEYS = {"kind", "code", "name", "activity", "delta_qty", "delta_value", "to_weight_pct", "delta_weight_pct", "period_end", "confidence"}


def test_moves_route_shape_matches_the_contract(session, pipeline_run):
    from instilens.api import deps
    from instilens.api.main import app
    from instilens.services import auth

    app.dependency_overrides[deps.get_session] = lambda: session
    client = TestClient(app)
    client.headers["authorization"] = f"Bearer {auth.issue_token(auth.register(session, 'm@example.com', 'password123', 'M'))}"
    for query in ("kind=buys&market=TR", "kind=exits&market=TR&fund=TMV&window=60&limit=5"):
        body = client.get(f"/api/v1/moves?{query}").json()
        assert set(body) == MOVES_KEYS, query
        assert body["market"] == "TR" and body["kind"] in analytics.MOVE_KINDS and isinstance(body["window_days"], int)
        assert date.fromisoformat(body["window_start"]) < date.fromisoformat(body["as_of"])
        assert body["fund"] is None or set(body["fund"]) == {"code", "name"}
        assert body["rows"] and all(set(r) == MOVE_ROW_KEYS for r in body["rows"]) and body["total"] >= len(body["rows"])
        if body["fund"] is not None:  # fund scope: the fund is every row's single party
            assert all(r["party_count"] == 1 and r["parties"][0]["code"] == body["fund"]["code"] for r in body["rows"])
        row = body["rows"][0]
        assert isinstance(row["net_flow_value"], float) and isinstance(row["net_qty"], int)
        assert all(isinstance(row[k], int) for k in ("funds_increasing", "funds_reducing", "funds_new", "funds_exited", "party_count"))
        assert 1 <= len(row["parties"]) <= 5 and row["party_count"] >= len(row["parties"])
        for p in row["parties"]:
            assert set(p) == MOVE_PARTY_KEYS
            assert p["kind"] in ("fund", "institution") and p["activity"] in ("NEW", "ADD", "REDUCE", "EXIT") and p["confidence"] in ("EXACT", "GROUPED", "INFERRED")
            assert isinstance(p["delta_qty"], int) and (p["delta_value"] is None or isinstance(p["delta_value"], float))
            assert all(p[k] is None or isinstance(p[k], float) for k in ("to_weight_pct", "delta_weight_pct"))
            date.fromisoformat(p["period_end"])
        assert [abs(p["delta_value"] or 0) for p in row["parties"]] == sorted((abs(p["delta_value"] or 0) for p in row["parties"]), reverse=True)
    app.dependency_overrides.clear()


def _symbol(session, instrument_id):
    from instilens.domain.models import Instrument

    return session.get(Instrument, instrument_id).symbol
