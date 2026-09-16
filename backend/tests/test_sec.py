"""SEC 13F path: real filings (fixtures/sec) → snapshots → INFERRED changes → US radar."""

from datetime import date

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
