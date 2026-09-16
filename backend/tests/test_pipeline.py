"""End-to-end: fixtures → raw → parsed → positions → signals/scores → API."""

from datetime import date
from decimal import Decimal

from fastapi.testclient import TestClient
from sqlalchemy import select

from instilens.domain.enums import ActivityType, Confidence, SignalType
from instilens.domain.models import Disclosure, PositionChange, Signal, TransactionEvent
from instilens.services import analytics, pipeline
from tests.conftest import AS_OF, FIXTURES


def test_chain_counts(pipeline_run):
    assert pipeline_run["ingested"] == 25
    assert pipeline_run["parsed"] == 25
    assert pipeline_run["position_changes"] > 0
    assert pipeline_run["instruments_scored"] >= 4


def test_amendment_supersedes_original(session, pipeline_run):
    old = session.scalar(select(Disclosure).where(Disclosure.source_id == "1608319"))
    new = session.scalar(select(Disclosure).where(Disclosure.source_id == "1608450"))
    assert old.is_superseded and new.supersedes_id == old.id
    old_ev = session.scalar(select(TransactionEvent).where(TransactionEvent.disclosure_id == old.id))
    new_ev = session.scalar(select(TransactionEvent).where(TransactionEvent.disclosure_id == new.id))
    assert old_ev.is_superseded and not new_ev.is_superseded
    assert new_ev.net_nominal == 46_526_835 and new_ev.confidence == Confidence.GROUPED
    assert {f.allocated_nominal for f in new_ev.funds} == {None}  # GROUPED → allocation unknown


def test_exact_event_is_allocated_to_its_fund(session, pipeline_run):
    ev = session.scalar(select(TransactionEvent).join(Disclosure).where(Disclosure.source_id == "1608402"))
    assert ev.confidence == Confidence.EXACT
    assert [f.allocated_nominal for f in ev.funds] == [1_200_000]


def test_first_snapshot_is_baseline_not_buying(session, pipeline_run):
    earliest = session.scalar(select(PositionChange).order_by(PositionChange.period_end).limit(1))
    assert earliest.period_start == date(2026, 5, 31)  # nothing before the first snapshot


def test_narrative_signals_detected(session, pipeline_run):
    types_by_symbol = {}
    for s in session.scalars(select(Signal)):
        symbol = analytics.stock_detail(session, "TR", _symbol(session, s.instrument_id))["symbol"]
        types_by_symbol.setdefault(symbol, set()).add(s.signal_type)
    assert {SignalType.ACCUMULATION, SignalType.POSITIVE_DIVERGENCE} <= types_by_symbol["ASELS"]
    assert SignalType.NEW_POSITION_CLUSTER in types_by_symbol["THYAO"]
    assert SignalType.EXIT_CLUSTER in types_by_symbol["SASA"]
    assert SignalType.DISTRIBUTION in types_by_symbol["EREGL"]


def test_dedup_rule_ignores_events_covered_by_snapshot(session, pipeline_run):
    detail = analytics.stock_detail(session, "TR", "ASELS")
    act = detail["scores"]["SMART_MONEY"]["why"]["activity"]
    # 08-20 buy (1607900) is covered by the 08-31 snapshot → only the GROUPED 09-12 sell adds to events.
    assert "EXACT" not in act["flow_by_confidence"]
    assert "GROUPED" in act["flow_by_confidence"] and "INFERRED" in act["flow_by_confidence"]
    assert act["funds_increasing"] == 5 and act["funds_reducing"] == 1  # 5 funds ADD + 1 grouped sell
    anele = analytics.stock_detail(session, "TR", "ANELE")["scores"]["SMART_MONEY"]["why"]["activity"]
    assert anele["funds_increasing"] == 1  # one GROUPED buy = one increasing party, never 2 funds
    assert {k: Decimal(v) for k, v in anele["flow_by_confidence"].items()} == {"GROUPED": Decimal("581585437.5")}  # Decimal, not str: trailing zeros vary by result processor


def test_radar_and_api(session, pipeline_run):
    radar = analytics.radar(session, "TR")
    assert {i["symbol"] for i in radar["accumulated"][:3]} == {"ANELE", "THYAO", "ASELS"}
    assert {i["symbol"] for i in radar["distributed"]} >= {"SASA", "EREGL"}
    assert all(0 <= i["smart_money_score"] <= 100 for i in radar["accumulated"])

    fund = analytics.fund_detail(session, "TMV")
    assert fund["activity"][ActivityType.NEW][0]["symbol"] == "THYAO"
    assert fund["activity"][ActivityType.EXIT][0]["symbol"] == "SASA"
    assert fund["events"][0]["confidence"] in {"EXACT", "GROUPED"}

    events = analytics.events(session, "TR")
    assert [e["source"]["id"] for e in events] == ["1608500", "1608450", "1608402", "1607900"]  # superseded hidden
    assert events[1]["allocation"] == "UNKNOWN" and events[2]["allocation"] == "EXACT"


def test_rerun_is_idempotent(session, pipeline_run):
    from instilens.ingestion.kap.fixture_adapter import KapFixtureAdapter

    again = pipeline.run_all(session, KapFixtureAdapter(FIXTURES / "kap"), AS_OF)
    assert again["ingested"] == 0 and again["parsed"] == 0
    assert again["position_changes"] == pipeline_run["position_changes"]
    assert session.scalar(select(TransactionEvent.id).where(TransactionEvent.is_superseded.is_(False)).order_by(TransactionEvent.id.desc())) is not None


def test_http_surface(session, pipeline_run, monkeypatch):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    client = TestClient(app)
    assert client.get("/health").json()["status"] == "ok"
    from instilens.services import auth

    token = auth.issue_token(auth.register(session, "t@example.com", "password123", "T"))
    client.headers["authorization"] = f"Bearer {token}"
    assert client.get("/api/v1/radar").json()["accumulated"][0]["symbol"] == "THYAO"
    assert client.get("/api/v1/stocks/ASELS").status_code == 200
    assert client.get("/api/v1/stocks/NOPE").status_code == 404
    assert client.get("/api/v1/funds/TMV").json()["institution"]["name"].startswith("Tera")
    assert len(client.get("/api/v1/events").json()) == 4
    app.dependency_overrides.clear()


def _symbol(session, instrument_id):
    from instilens.domain.models import Instrument
    return session.get(Instrument, instrument_id).symbol
