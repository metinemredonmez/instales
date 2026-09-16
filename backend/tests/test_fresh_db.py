"""A brand-new database must accept the CUSIP map before any ingest (markets table is empty)."""

import csv

from instilens.services.entities import EntityResolver
from tests.conftest import FIXTURES


def test_cusip_map_on_empty_database(session):
    with open(FIXTURES / "cusips_US.csv", newline="", encoding="utf-8") as f:
        assert EntityResolver(session).load_cusip_map(list(csv.DictReader(f))) > 100
    assert EntityResolver(session).instrument("TR", "ASELS").symbol == "ASELS"  # type: ignore[arg-type]  # str market accepted
