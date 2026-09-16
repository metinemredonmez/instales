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


# The label dictionary kap.org.tr pushes near the top of every page — it is a `disclosureDetail` too, and it comes
# BEFORE the filing's own detail block, so a parser that reads the first block only reads labels (see the real
# fixtures: `{"auditType": {"title": "Denetim Türü", …}, "opinionType": {…}}`).
DETAIL_LABELS = {
    "auditType": {"title": "Denetim Türü", "LT": "Sınırlı", "CT": "Sürekli", "OD": "Özel"},
    "opinionType": {"title": "Görüş Türü", "AC": "Görüş Bildirmekten Kaçınma", "OC": "Olumlu"},
}


def _rsc_page(body_html: str, basic: dict, detail: dict) -> str:
    """A minimal kap.org.tr detail page: the RSC payload is pushed as JSON string chunks, exactly like the real
    site — label dictionary first, then `disclosureBasic` with the filing's own `disclosureDetail` beside it."""
    import json

    full = (
        '{"cms":{"disclosureDetail":' + json.dumps(DETAIL_LABELS, ensure_ascii=False) + "}},"
        '{"disclosureBasic":' + json.dumps(basic, ensure_ascii=False) + ',"disclosureDetail":' + json.dumps(detail)
        + '},"disclosureBody":"' + body_html.replace('"', '\\"') + '"'
    )
    chunk = json.dumps(full, ensure_ascii=False)[1:-1]  # the site stores the payload as a JS string literal
    return '<html><body><script>self.__next_f.push([1,"' + chunk + '"])</script></body></html>'


CORRECTION_BODY = (
    "<div>İlgili Şirketler</div><div>Related Companies</div><div>[MARTI]</div>"
    "<div>İlgili Fonlar</div><div>Related Funds</div><div>[DOH]</div>"
    "<table><tr><td>oda_CorrectionAnnouncementFlag</td><td>Yapılan Açıklama Düzeltme mi?</td><td>Correction Notification Flag</td>"
    "<td></td><td></td><td>{answer}</td><td></td><td>{answer}</td></tr>"
    "<tr><td>oda_DateOfThePreviousNotificationAboutTheSameSubject</td><td>Konuya İlişkin Daha Önce Yapılan Açıklamanın Tarihi</td>"
    "<td>Date Of The Previous Notification About The Same Subject</td><td>{previous}</td></tr>"
    "<tr><td>oda_DelayedAnnouncementFlag</td><td>Yapılan Açıklama Ertelenmiş Bir Açıklama mı?</td><td>Hayır (No)</td></tr></table>"
    "<div>Transaction Date</div><table><tr><td>10/09/2026</td><td>10.313.894</td><td>0</td><td>10.313.894</td>"
    "<td>71.317.380</td><td>81.631.274</td><td>% 4,754492</td><td>% 4,754492</td><td>% 5,442085</td><td>% 5,442085</td></tr></table>"
)
BASIC = {
    "title": "Pay Alım Satım Bildirimi", "mkkMemberOid": "5553acdacf15471ba80c28eb45cdd9e7", "companyTitle": "TERA PORTFÖY YÖNETİMİ A.Ş.",
    "stockCode": "SKP", "publishDate": "2026.09.15 10:00:00", "disclosureIndex": 1662700, "attachmentCount": 0, "relatedDisclosureOid": "abc",
}


def test_correction_page_links_the_corrected_disclosure():
    page = _rsc_page(CORRECTION_BODY.format(answer="Evet (Yes)", previous="15.09.2026"), BASIC, {"relatedDisclosureIndex": 1662606, "oldKap": False})
    raw = parse_detail_page(page)
    assert raw is not None and raw.source_id == "1662700"
    assert raw.payload["is_correction"] is True and raw.payload["amends_source_id"] == "1662606"
    assert raw.payload["rows"][0]["nominal"] == 10_313_894 and raw.payload["related_fund_codes"] == ["DOH"]


def test_related_index_is_read_from_the_filing_block_not_the_label_dictionary():
    """The real pages carry two `disclosureDetail` blocks; only the one beside `disclosureBasic` holds the key."""
    from instilens.ingestion.kap.public_adapter import decode_rsc, detail_blocks

    blocks = detail_blocks(decode_rsc(_rsc_page(CORRECTION_BODY.format(answer="Evet (Yes)", previous="-"), BASIC, {"relatedDisclosureIndex": 1662606})))
    assert len(blocks) == 2 and "auditType" in blocks[0] and blocks[1]["relatedDisclosureIndex"] == 1662606
    for name in ("1662606", "1662620"):  # same two-block shape on the captured pages
        real = detail_blocks(decode_rsc((FIXTURES / "kap_public" / f"{name}.html").read_text(encoding="utf-8")))
        assert len(real) == 2 and "auditType" in real[0] and real[1]["relatedDisclosureIndex"] is None


def test_correction_index_falls_back_to_bildirim_link():
    body = CORRECTION_BODY.format(answer="Evet (Yes)", previous="<a href='/tr/Bildirim/1662606'>15.09.2026</a>")
    raw = parse_detail_page(_rsc_page(body, BASIC, {"relatedDisclosureIndex": None}))
    assert raw is not None and raw.payload["amends_source_id"] == "1662606"  # never the page's own index


def test_link_outside_the_correction_field_is_not_treated_as_the_corrected_disclosure():
    """A stray link would mark an unrelated disclosure superseded and drop its events from scoring."""
    body = CORRECTION_BODY.format(answer="Evet (Yes)", previous="-") + "<a href='/tr/Bildirim/1600001'>Şirketin diğer bildirimleri</a>"
    raw = parse_detail_page(_rsc_page(body, BASIC, {"relatedDisclosureIndex": None}))
    assert raw is not None and raw.payload["is_correction"] is True and raw.payload["amends_source_id"] is None


def test_ordinary_page_is_not_a_correction():
    raw = parse_detail_page(_rsc_page(CORRECTION_BODY.format(answer="Hayır (No)", previous="-"), BASIC, {"relatedDisclosureIndex": 1662606}))
    assert raw is not None and raw.payload["is_correction"] is False and raw.payload["amends_source_id"] is None
    real = parse_detail_page((FIXTURES / "kap_public" / "1662606.html").read_text(encoding="utf-8"))
    assert real.payload["is_correction"] is False and real.payload["amends_source_id"] is None
