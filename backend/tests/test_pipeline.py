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


def test_signals_do_not_duplicate_across_daily_computes(session, pipeline_run):
    """A signal that stays true is one episode: recompute the next day must extend it, not add a second row."""
    from datetime import timedelta

    from sqlalchemy import func, select

    from instilens.domain.models import Signal

    before = session.scalar(select(func.count(Signal.id)))
    assert before > 0
    pipeline.compute_intelligence(session, AS_OF + timedelta(days=1))
    session.commit()
    after = session.scalar(select(func.count(Signal.id)))
    assert after == before, f"signals duplicated: {before} -> {after}"
    assert session.scalar(select(func.max(Signal.window_end))) == AS_OF + timedelta(days=1)


def test_collapse_repairs_historic_duplicate_signals(session, pipeline_run):
    from datetime import timedelta

    from sqlalchemy import func, select

    from instilens.domain.models import Signal

    first = session.scalars(select(Signal)).first()
    dup = Signal(market_code=first.market_code, instrument_id=first.instrument_id, fund_id=None, signal_type=first.signal_type, strength=first.strength,
                 window_start=first.window_start + timedelta(days=1), window_end=first.window_end + timedelta(days=1), evidence=first.evidence, confidence=first.confidence)
    session.add(dup)
    session.flush()
    n = session.scalar(select(func.count(Signal.id)))
    assert pipeline.collapse_signal_episodes(session) == 1
    assert session.scalar(select(func.count(Signal.id))) == n - 1
    assert session.get(Signal, first.id).window_end == dup.window_end


def test_backfill_counts_event_until_its_covering_snapshot_is_known(session, pipeline_run):
    """Historical correctness: computing as of 2026-08-25 must not know about the 08-31 snapshot, so the 08-20 EXACT
    buy (1607900) counts then — and is covered (not counted) on 09-14, as `test_dedup_rule_ignores_events_covered_by_snapshot` asserts."""
    from instilens.domain.models import Instrument, Score

    asels = session.scalar(select(Instrument).where(Instrument.symbol == "ASELS"))
    past = date(2026, 8, 25)
    assert pipeline.latest_snapshot_as_of(session, past) != pipeline.latest_snapshot_as_of(session, AS_OF)
    pipeline.compute_intelligence(session, past)
    session.commit()

    def activity(on: date) -> dict:
        row = session.scalar(select(Score).where(Score.instrument_id == asels.id, Score.as_of == on, Score.score_type == "SMART_MONEY", Score.fund_id.is_(None)))
        return row.components["activity"]

    assert "EXACT" in activity(past)["flow_by_confidence"]  # the 08-20 buy was uncovered on 08-25
    assert "EXACT" not in activity(AS_OF)["flow_by_confidence"]  # by 09-14 the 08-31 snapshot is canonical


def _event(source_id: str, symbol: str, member: str, funds: list[str], on: str, side: str, nominal: int, before, after):
    from datetime import datetime

    from instilens.domain.enums import DisclosureKind, Market, Source
    from instilens.domain.schemas import RawDisclosure

    return RawDisclosure(
        market=Market.TR, source=Source.KAP, source_id=source_id, kind=DisclosureKind.KAP_SHARE_TRANSACTION,
        published_at=datetime.fromisoformat(on + "T18:00:00"),
        payload={"member_oid": f"MOID-{member}", "member_name": f"{member} Portföy", "subject_symbol": symbol, "related_fund_codes": funds,
                 "rows": [{"transaction_date": on, "side": side, "nominal": nominal, "price": 100}],
                 "ownership_before_pct": before, "ownership_after_pct": after},
    )


class _ListAdapter:
    name = "kap-list"

    def __init__(self, items):
        self.items = items

    def fetch(self, since_source_id=None):
        return sorted(self.items, key=lambda r: r.published_at)


def test_clusters_count_entries_and_exits_disclosed_by_events(session, pipeline_run):
    """NEW/EXIT clusters also see transaction events: 0 % → >0 % on a buy is an entry, → 0 % on a sell is an exit.
    Parties are de-duplicated against snapshot diffs and the evidence says where each party came from."""
    events = [
        # TUPRS: three EXACT first-time entries from three funds (no snapshot holds it) → NEW_POSITION_CLUSTER from events only
        _event("1609001", "TUPRS", "TERA", ["TMV"], "2026-09-08", "ALIS", 100_000, 0, "0.40"),
        _event("1609002", "TUPRS", "TERA", ["TLY"], "2026-09-09", "ALIS", 50_000, None, "0.20"),
        _event("1609003", "TUPRS", "IS", ["IPB"], "2026-09-10", "ALIS", 80_000, "0.0", "0.30"),
        # TUPRS: a fourth buy that is not an entry (already held) and a GROUPED buy whose funds are already counted → ignored
        _event("1609004", "TUPRS", "IS", ["TI2"], "2026-09-10", "ALIS", 80_000, "0.10", "0.30"),
        _event("1609005", "TUPRS", "TERA", ["TMV", "TLY"], "2026-09-11", "ALIS", 10_000, 0, "0.70"),
        # KCHOL: full exits by the three holders → EXIT_CLUSTER from events only
        _event("1609006", "KCHOL", "TERA", ["TMV"], "2026-09-09", "SATIS", 300_000, "0.30", 0),
        _event("1609007", "KCHOL", "TERA", ["TLY"], "2026-09-09", "SATIS", 100_000, "0.10", "0"),
        _event("1609008", "KCHOL", "IS", ["TI2"], "2026-09-10", "SATIS", 200_000, "0.20", "0.00"),
        # THYAO: TMV already entered per the 08-31 snapshot diff; an event entry for the same fund must not add a party
        _event("1609009", "THYAO", "TERA", ["TMV"], "2026-09-05", "ALIS", 10_000, 0, "0.40"),
        # EREGL: a GROUPED sell that does not reach 0 % and an EXACT partial sell → no exit party
        _event("1609010", "EREGL", "IS", ["IPB", "TI2"], "2026-09-10", "SATIS", 100_000, "0.50", "0.30"),
    ]
    out = pipeline.run_all(session, _ListAdapter(events), AS_OF)
    assert out["ingested"] == len(events) and out["parsed"] == len(events)
    session.commit()

    def signal(symbol: str, kind: SignalType) -> Signal | None:
        rows = [s for s in session.scalars(select(Signal).where(Signal.signal_type == kind)) if _symbol(session, s.instrument_id) == symbol]
        assert len(rows) <= 1
        return rows[0] if rows else None

    tuprs = signal("TUPRS", SignalType.NEW_POSITION_CLUSTER)
    assert tuprs is not None and tuprs.evidence["count"] == 3
    assert tuprs.evidence["from_events"] == ["IPB", "TLY", "TMV"] and tuprs.evidence["from_snapshots"] == []
    assert tuprs.evidence["funds"] == ["IPB", "TLY", "TMV"]
    assert (tuprs.window_start, tuprs.window_end) == (date(2026, 9, 8), AS_OF)

    kchol = signal("KCHOL", SignalType.EXIT_CLUSTER)
    assert kchol is not None and kchol.evidence["from_events"] == ["TI2", "TLY", "TMV"] and kchol.evidence["from_snapshots"] == []

    thyao = signal("THYAO", SignalType.NEW_POSITION_CLUSTER)
    assert thyao is not None and thyao.evidence["count"] == 3  # TMV counted once
    assert thyao.evidence["from_snapshots"] == ["IPB", "TLY", "TMV"] and thyao.evidence["from_events"] == []

    assert signal("EREGL", SignalType.EXIT_CLUSTER) is None
    assert signal("ANELE", SignalType.NEW_POSITION_CLUSTER) is None  # one GROUPED entry (1608450) is one party, not a cluster


def test_grouped_entry_counts_as_one_institution_party(session, pipeline_run):
    from instilens.domain.models import Instrument

    events = [
        _event("1609101", "TUPRS", "TERA", ["TMV", "TLY"], "2026-09-08", "ALIS", 100_000, 0, "0.40"),  # GROUPED → party is the institution
        _event("1609102", "TUPRS", "IS", ["IPB"], "2026-09-09", "ALIS", 50_000, 0, "0.20"),
        _event("1609103", "TUPRS", "MARMARA", ["MAC"], "2026-09-10", "ALIS", 80_000, 0, "0.30"),
    ]
    pipeline.run_all(session, _ListAdapter(events), AS_OF)
    session.commit()
    tuprs = session.scalar(select(Instrument).where(Instrument.symbol == "TUPRS"))
    sig = session.scalar(select(Signal).where(Signal.instrument_id == tuprs.id, Signal.signal_type == SignalType.NEW_POSITION_CLUSTER))
    assert sig is not None and sig.evidence["count"] == 3
    assert sig.evidence["from_events"] == ["IPB", "MAC"] + [p for p in sig.evidence["from_events"] if p.startswith("inst:")]
    assert len([p for p in sig.evidence["funds"] if p.startswith("inst:")]) == 1  # TMV and TLY are never listed separately


def test_event_entries_reach_the_score_components_and_the_leaderboard(session, pipeline_run):
    """The Consensus tilt and the leaderboard's funds_new/funds_exited count the same parties the cluster does:
    a first-time entry disclosed by an event is one party there too."""
    from instilens.domain.models import Instrument, Score

    events = [
        _event("1609201", "TUPRS", "TERA", ["TMV"], "2026-09-08", "ALIS", 100_000, 0, "0.40"),
        _event("1609202", "TUPRS", "TERA", ["TLY"], "2026-09-09", "ALIS", 50_000, None, "0.20"),
        _event("1609203", "TUPRS", "IS", ["IPB"], "2026-09-10", "ALIS", 80_000, "0.0", "0.30"),
    ]
    pipeline.run_all(session, _ListAdapter(events), AS_OF)
    session.commit()
    tuprs = session.scalar(select(Instrument).where(Instrument.symbol == "TUPRS"))
    row = session.scalar(select(Score).where(Score.instrument_id == tuprs.id, Score.as_of == AS_OF, Score.score_type == "CONSENSUS", Score.fund_id.is_(None)))
    assert row.components["activity"]["funds_new"] == 3 and row.components["activity"]["funds_exited"] == 0
    assert row.components["components"]["tilt"] > 0  # three entries, nobody leaving
    flows = analytics.window_flows(session, "TR", 30, AS_OF)
    (leader,) = [r for r in flows["accumulated"] if r["symbol"] == "TUPRS"]
    assert leader["funds_new"] == 3 and leader["funds_exited"] == 0 and leader["funds_increasing"] == 3


def test_report_correction_with_a_new_as_of_leaves_one_snapshot(session, pipeline_run):
    """A KAP düzeltme that also fixes the report date: the superseded report's snapshot must go with it, or the
    fund would carry two snapshots for one period — and the position chain would diff the correction against it."""
    import json

    from instilens.domain.models import Fund, PortfolioSnapshot
    from instilens.domain.schemas import RawDisclosure

    original = RawDisclosure.model_validate(json.loads((FIXTURES / "kap" / "1601001.json").read_text(encoding="utf-8")))
    fund = session.scalar(select(Fund).where(Fund.code == original.payload["fund_code"]))
    before = {s.as_of for s in session.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == fund.id))}
    assert date(2026, 5, 31) in before

    correction = original.model_copy(update={
        "source_id": "1601099",
        "payload": {**original.payload, "as_of": "2026-06-01", "is_correction": True, "amends_source_id": original.source_id},
    })
    out = pipeline.run_all(session, _ListAdapter([correction]), AS_OF)
    assert out["ingested"] == 1 and out["parsed"] == 1
    session.commit()

    old = session.scalar(select(Disclosure).where(Disclosure.source_id == original.source_id))
    new = session.scalar(select(Disclosure).where(Disclosure.source_id == "1601099"))
    assert old.is_superseded and new.supersedes_id == old.id
    dates = {s.as_of: s.disclosure_id for s in session.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == fund.id))}
    assert date(2026, 5, 31) not in dates and dates[date(2026, 6, 1)] == new.id
    assert len(dates) == len(before)  # the corrected report replaced the original, it did not add a period
