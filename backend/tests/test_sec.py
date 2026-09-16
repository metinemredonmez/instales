"""SEC 13F path: real filings (fixtures/sec) → snapshots → INFERRED changes → US radar."""

from datetime import date, datetime

from instilens.domain.enums import Market
from instilens.ingestion.sec.edgar_client import parse_information_table, to_raw_disclosure
from instilens.ingestion.sec.fixture_adapter import SecFixtureAdapter
from instilens.parsing.sec_13f import parse_13f
from instilens.services import analytics, pipeline
from instilens.services.entities import EntityResolver
from tests.conftest import FIXTURES

XML = """<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>1000</value>
<shrsOrPrnAmt><sshPrnamt>10</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>037833100</cusip><value>500</value>
<shrsOrPrnAmt><sshPrnamt>5</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
<infoTable><nameOfIssuer>APPLE INC</nameOfIssuer><titleOfClass>CALL</titleOfClass><cusip>037833100</cusip><value>99</value>
<shrsOrPrnAmt><sshPrnamt>1</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt><putCall>Call</putCall></infoTable>
</informationTable>"""


def test_information_table_parse_and_merge():
    rows = parse_information_table(XML)
    assert len(rows) == 3 and rows[2]["put_call"] == "Call"
    raw = to_raw_disclosure({"cik": "320193", "name": "X", "accession": "0000-1", "filed": "2026-08-14", "period": "2026-06-30", "amendment": False}, rows)
    (h,) = raw.payload["holdings"]  # two share rows merged, option row dropped
    assert h["quantity"] == 15 and h["value_usd"] == 1500 and h["weight_pct"] == 100.0
    snap = parse_13f(raw)
    assert snap.fund_code == "CIK320193" and snap.holdings[0].instrument_symbol == "CUSIP:037833100"


def test_us_pipeline_on_real_filings(session, pipeline_run):
    import csv

    with open(FIXTURES / "cusips_US.csv", newline="", encoding="utf-8") as f:
        assert EntityResolver(session).load_cusip_map(list(csv.DictReader(f))) > 100
    out = pipeline.run_all(session, SecFixtureAdapter(FIXTURES / "sec"), date(2026, 9, 14), market=Market.US)
    assert out["ingested"] == 9 and out["parsed"] == 9 and out["position_changes"] > 5000
    radar = analytics.radar(session, "US", 10)
    assert radar["window_days"] == 100
    symbols = {r["symbol"] for r in radar["accumulated"]} | {r["symbol"] for r in radar["distributed"]}
    assert "GOOGL" in symbols and "BAC" in symbols  # Berkshire Q2-2026: added Alphabet, trimmed Bank of America
    assert all(r["confidence_multiplier"] == 0.8 for r in radar["accumulated"])  # 13F diffs are INFERRED only
    fresh = analytics.data_freshness(session, "US")
    assert fresh[0]["source"] == "SEC 13F" and fresh[0]["delayed"] and fresh[0]["last"] == "2026-06-30"
    inst = analytics.institutions(session, "US")
    assert {i["funds"] for i in inst} == {1} and len(inst) == 3
    # TR untouched by the US run
    assert {r["symbol"] for r in analytics.radar(session, "TR", 5)["accumulated"]} >= {"ASELS"}


class _ListAdapter:
    name = "sec-list"

    def __init__(self, items):
        self.items = items

    def fetch(self, since_source_id=None):
        return sorted(self.items, key=lambda r: r.published_at)


def _berkshire():
    import json

    from instilens.domain.schemas import RawDisclosure

    return [RawDisclosure.model_validate(json.loads((FIXTURES / "sec" / f"{acc}.json").read_text(encoding="utf-8")))
            for acc in ("0001193125-26-054580", "0001193125-26-226661", "0001193125-26-352200")]


def test_link_amendments_names_the_original_of_the_same_period():
    from instilens.ingestion.sec.edgar_client import link_amendments

    batch = [
        {"cik": "1067983", "name": "B", "accession": "0001193125-26-352200", "filed": "2026-08-14", "period": "2026-06-30", "amendment": False},
        {"cik": "1067983", "name": "B", "accession": "0001193125-26-226661", "filed": "2026-05-15", "period": "2026-03-31", "amendment": False},
        {"cik": "1067983", "name": "B", "accession": "0000950123-26-400000", "filed": "2026-09-01", "period": "2026-06-30", "amendment": True},
        {"cik": "1067983", "name": "B", "accession": "0000950123-26-100000", "filed": "2026-02-01", "period": "2025-12-31", "amendment": True},
    ]
    linked = {f["accession"]: f["amends"] for f in link_amendments(batch)}
    assert linked["0000950123-26-400000"] == "0001193125-26-352200"  # same CIK + period, filed earlier — regardless of accession prefix
    assert linked["0001193125-26-352200"] is None and linked["0000950123-26-100000"] is None  # original unknown → nothing to supersede
    raw = to_raw_disclosure({**batch[2], "amends": "0001193125-26-352200"}, [])
    assert raw.payload["amends_source_id"] == "0001193125-26-352200"


def test_13f_amendment_supersedes_original_and_keeps_one_snapshot(session):
    from sqlalchemy import func, select

    from instilens.domain.models import Disclosure, Fund, PortfolioSnapshot, PositionChange

    originals = _berkshire()
    first = pipeline.run_all(session, _ListAdapter(originals), date(2026, 9, 14), market=Market.US)
    assert first["ingested"] == 3 and first["position_changes"] > 0
    fund = session.scalar(select(Fund).where(Fund.code == "CIK1067983"))
    assert session.scalar(select(func.count(PortfolioSnapshot.id)).where(PortfolioSnapshot.fund_id == fund.id)) == 3

    # A 13F-HR/A for Q2 arrives in a later run; the fixture adapter's shape carries no `amends_source_id`.
    q2_raw = originals[2]
    amendment = q2_raw.model_copy(update={
        "source_id": "0000950123-26-400000", "published_at": datetime(2026, 9, 1),
        "payload": {**q2_raw.payload, "amendment": True, "holdings": q2_raw.payload["holdings"][:-1]},
    })
    second = pipeline.run_all(session, _ListAdapter(originals + [amendment]), date(2026, 9, 14), market=Market.US)
    assert second["ingested"] == 1 and second["parsed"] == 1
    old = session.scalar(select(Disclosure).where(Disclosure.source_id == q2_raw.source_id))
    new = session.scalar(select(Disclosure).where(Disclosure.source_id == "0000950123-26-400000"))
    assert old.is_superseded and new.supersedes_id == old.id
    snaps = session.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == fund.id, PortfolioSnapshot.as_of == date(2026, 6, 30))).all()
    assert len(snaps) == 1 and snaps[0].disclosure_id == new.id and len(snaps[0].holdings) == len(q2_raw.payload["holdings"]) - 1
    assert session.scalar(select(func.count(PortfolioSnapshot.id)).where(PortfolioSnapshot.fund_id == fund.id)) == 3
    # positions were rebuilt on the amended chain: Q1 → Q2/A and Q2/A → nothing (Q2 is the latest period)
    assert session.scalar(select(func.count(PositionChange.id)).where(PositionChange.to_snapshot_id == snaps[0].id)) > 0
    assert session.scalar(select(func.count(PositionChange.id)).where(PositionChange.from_snapshot_id == snaps[0].id)) == 0


def test_new_holdings_amendment_names_no_original():
    from instilens.ingestion.sec.edgar_client import link_amendments

    batch = [
        {"cik": "1067983", "name": "B", "accession": "0001193125-26-352200", "filed": "2026-08-14", "period": "2026-06-30", "amendment": False},
        {"cik": "1067983", "name": "B", "accession": "0000950123-26-400000", "filed": "2026-09-01", "period": "2026-06-30", "amendment": True, "amendment_type": "NEW HOLDINGS"},
        {"cik": "1067983", "name": "B", "accession": "0000950123-26-400001", "filed": "2026-09-02", "period": "2026-06-30", "amendment": True, "amendment_type": "RESTATEMENT"},
    ]
    linked = {f["accession"]: f["amends"] for f in link_amendments(batch)}
    assert linked["0000950123-26-400000"] is None  # adds positions to the original — nothing to supersede
    assert linked["0000950123-26-400001"] == "0000950123-26-400000"  # a restatement replaces the newest filing of that period


def test_amendment_type_comes_from_the_cover_page():
    import httpx

    from instilens.ingestion.sec.edgar_client import EdgarClient

    cover = "<edgarSubmission><headerData><submissionType>13F-HR/A</submissionType></headerData>" \
            "<formData><coverPage><amendmentInfo><amendmentType>NEW HOLDINGS</amendmentType></amendmentInfo></coverPage></formData></edgarSubmission>"

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=cover) if "000095012326400000" in request.url.path else httpx.Response(404)

    client = EdgarClient("test", client=httpx.Client(transport=httpx.MockTransport(handler)))
    assert client.amendment_type("1067983", "0000950123-26-400000") == "NEW HOLDINGS"
    assert client.amendment_type("1067983", "0000950123-26-999999") is None  # unreadable cover page → historical default


def test_new_holdings_amendment_completes_the_original_snapshot(session):
    from sqlalchemy import select

    from instilens.domain.models import Disclosure, Fund, PortfolioSnapshot

    originals = _berkshire()
    pipeline.run_all(session, _ListAdapter(originals), date(2026, 9, 14), market=Market.US)
    fund = session.scalar(select(Fund).where(Fund.code == "CIK1067983"))
    q2 = originals[2]
    snap = session.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == fund.id, PortfolioSnapshot.as_of == date(2026, 6, 30)))
    held_before = len(snap.holdings)

    # A NEW HOLDINGS amendment lists only the position the original left out — it must not replace the book.
    amendment = q2.model_copy(update={
        "source_id": "0000950123-26-400001", "published_at": datetime(2026, 9, 2),
        "payload": {**q2.payload, "amendment": True, "amendment_type": "NEW HOLDINGS",
                    "holdings": [{"cusip": "88160R101", "issuer": "TESLA INC", "quantity": 1_000, "value_usd": 250_000, "weight_pct": 100.0}]},
    })
    out = pipeline.run_all(session, _ListAdapter(originals + [amendment]), date(2026, 9, 14), market=Market.US)
    assert out["ingested"] == 1 and out["parsed"] == 1
    old = session.scalar(select(Disclosure).where(Disclosure.source_id == q2.source_id))
    new = session.scalar(select(Disclosure).where(Disclosure.source_id == "0000950123-26-400001"))
    assert not old.is_superseded and new.supersedes_id is None  # additive amendments never supersede
    snaps = session.scalars(select(PortfolioSnapshot).where(PortfolioSnapshot.fund_id == fund.id, PortfolioSnapshot.as_of == date(2026, 6, 30))).all()
    assert len(snaps) == 1 and len(snaps[0].holdings) == held_before + 1
    assert abs(sum(h.weight_pct for h in snaps[0].holdings) - 100) < 1  # weights recomputed over the completed book
