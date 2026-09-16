"""Prototype KAP public-site adapter: parse real saved detail pages (fixtures/kap_public)."""

from instilens.domain.enums import Confidence
from instilens.ingestion.kap.public_adapter import parse_detail_page
from instilens.parsing import parse_share_transaction
from tests.conftest import FIXTURES


def test_parse_real_tera_disclosure():
    raw = parse_detail_page((FIXTURES / "kap_public" / "1662606.html").read_text(encoding="utf-8"))
    assert raw is not None and raw.source_id == "1662606"
    p = raw.payload
    assert p["member_name"].startswith("TERA PORTFÖY") and p["member_oid"]
    assert p["subject_symbol"] == "MARTI" and p["related_fund_codes"] == ["DOH"]
    assert p["rows"] == [{"transaction_date": "2026-09-10", "side": "ALIS", "nominal": 10_313_894, "price": None}]
    assert p["ownership_before_pct"] == "4.754492" and p["ownership_after_pct"] == "5.442085"
    ev = parse_share_transaction(raw)
    assert ev.confidence is Confidence.EXACT and ev.fund_allocations == {"DOH": 10_313_894}


def test_prose_only_disclosure_falls_back_to_text():
    # Marmara Capital put the numbers in text + PDF; the table is empty → numbers come from prose, flagged as such.
    raw = parse_detail_page((FIXTURES / "kap_public" / "1662620.html").read_text(encoding="utf-8"))
    assert raw is not None and raw.payload["numbers_from"] == "prose" and raw.payload["related_fund_codes"] == ["MAC", "MAS"]


def test_prose_fallback_extracts_marmara_numbers():
    from instilens.ingestion.kap.public_adapter import rows_from_prose

    text = "GSD Holding A.Ş. (GSDHO) payları toplamı, 11.09.2026 tarihinde 4,67-4,70 fiyat aralığından (ortalama fiyat 4,676882) 3.027.970 adet satış işlemi sonucunda şirket sermayesi içindeki oranı %3.14'den %2,84'e düşmüş"
    rows, before, after = rows_from_prose(text)
    assert rows == [{"transaction_date": "2026-09-11", "side": "SATIS", "nominal": 3_027_970}]
    assert (before, after) == ("3.14", "2.84")
    raw = parse_detail_page((FIXTURES / "kap_public" / "1662620.html").read_text(encoding="utf-8"))
    assert raw is not None and raw.payload["numbers_from"] == "prose"
    assert raw.payload["rows"][0]["nominal"] == 3_027_970 and raw.payload["rows"][0]["price"] == "4.676882"


def test_pdr_pdf_parser_on_real_report():
    from datetime import datetime

    from instilens.domain.enums import DisclosureKind, Market, Source
    from instilens.domain.schemas import RawDisclosure
    from instilens.ingestion.kap.pdr_pdf import parse_pdr_pdf
    from instilens.parsing import parse_portfolio_report

    payload = parse_pdr_pdf((FIXTURES / "kap_public" / "VPS_pdr_2026w36.pdf").read_bytes())
    assert payload["fund_code"] == "VPS" and payload["as_of"] == "2026-09-11" and payload["member_name"].startswith("VEGA")
    syms = {h["symbol"] for h in payload["holdings"]}
    assert {"ASELS", "THYAO", "BIMAS", "SOKE"} <= syms and len(syms) >= 18
    total = sum(float(h["market_value"]) for h in payload["holdings"]) / float(payload["total_value"])
    assert 0.75 < total < 0.95  # equity block ≈ NAV share reported for the week
    snap = parse_portfolio_report(RawDisclosure(market=Market.TR, source=Source.KAP, source_id="x", kind=DisclosureKind.KAP_PORTFOLIO_REPORT, published_at=datetime(2026, 9, 14), payload=payload))
    assert snap.fund_code == "VPS" and len(snap.holdings) == len(syms)
