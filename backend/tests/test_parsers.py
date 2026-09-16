from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from instilens.domain.enums import Confidence, DisclosureKind, Market, Side, Source
from instilens.domain.schemas import RawDisclosure
from instilens.parsing import parse_portfolio_report, parse_share_transaction


def _tx(funds, rows):
    return RawDisclosure(
        market=Market.TR, source=Source.KAP, source_id="1", kind=DisclosureKind.KAP_SHARE_TRANSACTION,
        published_at=datetime(2026, 9, 10, 19, 37, tzinfo=UTC),
        payload={"member_oid": "M1", "member_name": "Tera", "subject_symbol": "anele",
                 "related_fund_codes": funds, "rows": rows},
    )


def test_single_fund_is_exact_and_allocated():
    ev = parse_share_transaction(_tx(["TMV"], [{"transaction_date": "2026-09-10", "side": "ALIS", "nominal": 100, "price": "12.5"}]))
    assert ev.confidence is Confidence.EXACT
    assert ev.fund_allocations == {"TMV": 100}
    assert ev.side is Side.BUY
    assert ev.net_value == Decimal("1250.0")
    assert ev.instrument_symbol == "ANELE"


def test_multiple_funds_is_grouped_and_never_split():
    ev = parse_share_transaction(_tx(["TMV", "TLY", "T3B"], [{"transaction_date": "2026-09-10", "side": "ALIS", "nominal": 10_000_000}]))
    assert ev.confidence is Confidence.GROUPED
    assert ev.fund_allocations == {"T3B": None, "TLY": None, "TMV": None}  # allocation UNKNOWN
    assert ev.net_value is None  # no price → no value, we don't guess


def test_no_funds_is_grouped_at_institution_level():
    ev = parse_share_transaction(_tx([], [{"transaction_date": "2026-09-10", "side": "SATIS", "nominal": 5}]))
    assert ev.confidence is Confidence.GROUPED
    assert ev.side is Side.SELL and ev.net_nominal == -5


def test_mixed_rows_net_and_weighted_price():
    ev = parse_share_transaction(_tx(["TMV"], [
        {"transaction_date": "2026-09-09", "side": "ALIS", "nominal": 100, "price": "10"},
        {"transaction_date": "2026-09-10", "side": "SATIS", "nominal": 40, "price": "20"},
    ]))
    assert ev.side is Side.MIXED
    assert (ev.buy_nominal, ev.sell_nominal, ev.net_nominal) == (100, 40, 60)
    assert ev.avg_price == Decimal("100") * 10 / 140 * 1 + Decimal("40") * 20 / 140  # nominal-weighted
    assert ev.effective_date == date(2026, 9, 10)


def test_portfolio_report_merges_duplicate_lines():
    raw = RawDisclosure(
        market=Market.TR, source=Source.KAP, source_id="2", kind=DisclosureKind.KAP_PORTFOLIO_REPORT,
        published_at=datetime(2026, 8, 31, tzinfo=UTC), payload={"fund_code": "tmv", "as_of": "2026-08-31", "holdings": [
            {"symbol": "ASELS", "quantity": 100, "weight_pct": "1.5"},
            {"symbol": "asels", "quantity": 50, "weight_pct": "0.5"},
            {"symbol": "THYAO", "quantity": 10},
        ]},
    )
    snap = parse_portfolio_report(raw)
    assert snap.fund_code == "TMV"
    assert [(h.instrument_symbol, h.quantity) for h in snap.holdings] == [("ASELS", 150), ("THYAO", 10)]
    assert snap.holdings[0].weight_pct == Decimal("2.0")


def test_rows_required():
    with pytest.raises(ValidationError):
        parse_share_transaction(_tx(["TMV"], []))
