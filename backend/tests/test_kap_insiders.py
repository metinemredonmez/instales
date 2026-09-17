"""KAP insider filings (persons / shareholders, part A of the TR insiders contract): the page and SPK-form parsers on
real kap.org.tr fixtures (fixtures/kap_public, see its README), the normaliser's role mapping, the refresh loader
behind an httpx MockTransport (idempotent, apart from the PYŞ path), correction supersession, the buyback rule, the
TR route shape, the cluster and the alert on a BIST watchlist, the scheduler gate and the migration. Network-free."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from instilens.config import settings
from instilens.domain.enums import DisclosureKind, Market, SignalType
from instilens.domain.models import (
    Disclosure,
    InsiderTransaction,
    Instrument,
    Notification,
    Signal,
    TransactionEvent,
    Watchlist,
    WatchlistItem,
)
from instilens.ingestion.kap import public_adapter
from instilens.ingestion.kap.public_adapter import (
    BASE,
    KapPublicAdapter,
    parse_insider_page,
    party_kind,
    prices_in,
)
from instilens.parsing.kap_insider import (
    ISSUER_ROLE,
    parse_insider_filing,
    party_key,
    roles_from_title,
)
from instilens.services import alerts, analytics, auth, insiders, pipeline
from tests.conftest import FIXTURES

KAP = FIXTURES / "kap_public"
GIPTA_SALE = "1654800"  # Mehmet Sönmez (GENEL MÜDÜR): SATIŞ 60.000 @ 103,2000 on 2026-08-25, stake after 0,0758 % — relayed by MKK, form PDF
BURVA_BUY = "1662850"  # Ümit Gümüş (Yönetim Kurulu Başkanı): ALIŞ 30.000 in 594–600 on 2026-09-15, stake after 55,14 % — relayed, form PDF
ATSYH_BUY = "1664326"  # Süleyman Yıldırım, chairman, on the issuer's own page: ALIŞ 31.879 in 103,90–104,00 on 2026-09-16, 1,6394 % → 2,0379 %
AKSA_BUY = "1664407"  # Akkök Holding A.Ş. (legal entity, signatory Ayberk Büyükbayram): ALIŞ 1.750.000 in 10.41–10.69 on 2026-09-16, stake after 40,22 %
TERA_PYS = "1662606"  # the PYŞ filing test_kap_public reads: on the pipeline's path, never on this one
PDFS = {  # attachment objId → fixture file, exactly as /tr/api/file/download/{objId} served it
    "4028328c9f52dc3f01a038c2687f265a": "1654800_GIPTA_55984.pdf",
    "4028328da09bf08e01a0a494fe4f4031": "1662850_BURVA_56128.pdf",
    "4028328ca09bee8f01a0ade645751699": "1664407_AKSA_56154.pdf",
}
AS_OF = date(2026, 9, 20)


def _page(index: str) -> str:
    return (KAP / f"{index}.html").read_text(encoding="utf-8")


def _attachment(obj_id: str) -> bytes:
    return (KAP / PDFS[obj_id]).read_bytes()


def _parse(index: str):
    return parse_insider_page(_page(index), fetch_attachment=_attachment)


class _Kap:
    """kap.org.tr behind httpx.MockTransport: the real listing rows for byCriteria (whatever the dates asked), the
    fixture pages by index, the PDFs by objId, an empty fund-report listing; everything else 404. `pages` can be
    extended per test; `requests` records every call."""

    def __init__(self) -> None:
        self.listing = json.loads((KAP / "byCriteria_share_transactions.json").read_text(encoding="utf-8"))
        self.pages = {idx: _page(idx) for idx in (GIPTA_SALE, BURVA_BUY, ATSYH_BUY, AKSA_BUY, TERA_PYS, "1662620")}
        self.pdfs = dict(PDFS)
        self.requests: list[str] = []

    def adapter(self, **kw) -> KapPublicAdapter:
        client = httpx.Client(transport=httpx.MockTransport(self), base_url=BASE)
        return KapPublicAdapter(client=client, delay_seconds=0, days_back=30, **kw)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        self.requests.append(path)
        if path == "/tr/api/disclosure/members/byCriteria":
            return httpx.Response(200, json=self.listing, request=request)
        if path == "/tr/api/disclosure/funds/byCriteria":
            return httpx.Response(200, json=[], request=request)
        if path.startswith("/tr/Bildirim/"):
            idx = path.rsplit("/", 1)[-1]
            return httpx.Response(200, text=self.pages[idx], request=request) if idx in self.pages else httpx.Response(404, request=request)
        if path.startswith("/tr/api/file/download/"):
            obj = path.rsplit("/", 1)[-1]
            return httpx.Response(200, content=(KAP / self.pdfs[obj]).read_bytes(), request=request) if obj in self.pdfs else httpx.Response(404, request=request)
        return httpx.Response(404, request=request)


# --- parsers on the real fixtures ------------------------------------------------------------------------------------


def test_parse_relayed_filings_read_the_spk_form():
    """The MKK-relayed page names only the sender; the form PDF carries the party, the role, the numbers, the stake."""
    sale = _parse(GIPTA_SALE)
    assert sale is not None and sale.kind is DisclosureKind.KAP_INSIDER_TRANSACTION and sale.market is Market.TR and sale.source_id == GIPTA_SALE
    assert sale.raw_uri == f"https://www.kap.org.tr/tr/Bildirim/{GIPTA_SALE}" and sale.published_at == datetime(2026, 8, 25, 14, 52, 55)
    p = sale.payload
    assert p["member_name"] == "KAMUYU AYDINLATMA PLATFORMU" and p["relayed"] is True and p["subject_symbol"] == "GIPTA"
    assert p["subject_name"] == "GIPTA OFİS KIRTASİYE VE PROMOSYON ÜRÜNLERİ İMALAT SANAYİ A.Ş." and p["attachment_count"] == 1
    assert (p["party_name"], p["form_party_name"], p["party_kind"], p["role_text"], p["signatory"], p["party_is_issuer"]) == ("Mehmet Sönmez", "MEHMET SÖNMEZ", "person", "GENEL MÜDÜR", None, False)
    assert p["rows"] == [{"transaction_date": "2026-08-25", "side": "SATIS", "nominal": 60_000, "price": "103.2000", "price_low": None, "price_high": None, "post_pct_stake": "0.0758"}]
    assert p["post_pct_stake"] == "0.0758" and p["is_correction"] is False and p["amends_source_id"] is None and p["numbers_from"] == "form"

    buy = _parse(BURVA_BUY)
    p = buy.payload
    assert (p["subject_symbol"], p["party_name"], p["party_kind"], p["role_text"], p["signatory"]) == ("BURVA", "Ümit Gümüş", "person", "Yönetim Kurulu Başkanı", None)
    assert p["rows"] == [{"transaction_date": "2026-09-15", "side": "ALIS", "nominal": 30_000, "price": None, "price_low": "594", "price_high": "600", "post_pct_stake": "55.14"}]  # a range stays a range

    entity = _parse(AKSA_BUY)
    p = entity.payload
    assert (p["subject_symbol"], p["party_name"], p["form_party_name"], p["party_kind"]) == ("AKSA", "Akkök Holding A.Ş.", "AKKÖK HOLDİNG A.Ş.", "company")
    assert (p["signatory"], p["signatory_role"], p["role_text"]) == ("Ayberk Büyükbayram", "Mali İşler ve Vergi Yönetimi Direktörü", None)  # the form's Görevi is the signatory's job
    assert p["rows"] == [{"transaction_date": "2026-09-16", "side": "ALIS", "nominal": 1_750_000, "price": None, "price_low": "10.41", "price_high": "10.69", "post_pct_stake": "40.22"}]
    # Without the attachment (the site did not serve it) the page alone gives the sender and the symbol; the numbers are missing.
    bare = parse_insider_page(_page(BURVA_BUY))
    assert bare.payload["party_name"] == "Ümit Gümüş" and bare.payload["rows"] == [] and bare.payload["subject_symbol"] == "BURVA"
    assert parse_insider_page(_page(TERA_PYS)) is not None  # the same subject: the caller keeps the paths apart, not the parser
    assert parse_insider_page("<html></html>") is None


def test_parse_issuer_page_reads_the_prose_and_the_table():
    """Atlantis Yatırım Holding's own page: the chairman and his title in the prose, the numbers in the standard table."""
    raw = _parse(ATSYH_BUY)
    p = raw.payload
    assert p["member_name"] == "ATLANTİS YATIRIM HOLDİNG A.Ş." and p["member_code"] == "ATSYH" and p["subject_symbol"] == "ATSYH" and p["relayed"] is False
    assert (p["party_name"], p["party_kind"], p["role_text"], p["party_is_issuer"], p["numbers_from"]) == ("Süleyman Yıldırım", "person", "Yönetim Kurulu Başkanı", False, "table")
    assert p["rows"] == [{"transaction_date": "2026-09-16", "side": "ALIS", "nominal": 31_879, "price": None, "price_low": "103.90", "price_high": "104.00", "post_pct_stake": "2.0379"}]
    assert p["summary"] == "Yönetim Kurulu Başkanı Tarafından Gerçekleştirilen Pay Alımı" and p["is_correction"] is False
    # The pure helpers on the phrasings kap.org.tr prints.
    assert prices_in("103,2000 TL fiyatından 60.000 TL toplam nominal tutarlı") == ("103.2000", None, None)
    assert prices_in("1,08-1,15-TL fiyat aralığından (ortalama 1,125) alış işlemi") == ("1.125", "1.08", "1.15")  # a stated average is the price; the range is kept
    assert prices_in("16,46 - 16,46 TL fiyat aralığından") == ("16.46", None, None) and prices_in("2,22 TL'den satılmıştır") == ("2.22", None, None)
    assert prices_in("2,69 ile 3,00  TL aralığında satılmıştır") == (None, "2.69", "3.00") and prices_in("10.41 TL-10.69 TL fiyat aralığından") == (None, "10.41", "10.69")
    assert prices_in("hisse alımı yapılmıştır") == (None, None, None)  # nothing stated: nothing made up
    # A stake is never a price ("%1,63'ten %2,03'e"), nor is a bare count ("10.000'den fazla"): the currency or "fiyat" is mandatory.
    assert prices_in("pay oranı %1,63'ten %2,03'e yükselmiştir") == (None, None, None) and prices_in("pay oranı %1,6394'ten yaklaşık %2,0379 seviyesine yükselmiştir.") == (None, None, None)
    assert prices_in("10.000'den fazla pay") == (None, None, None) and prices_in("37,56 fiyattan alınmıştır") == ("37.56", None, None)
    one_day = {"prose": "Şirketimiz Yönetim Kurulu Üyesi Sn. X tarafından 16.09.2026 tarihinde 10.000 adet alış işlemi gerçekleştirilmiş olup, pay oranı %1,63'ten %2,03'e yükselmiştir.",
               "rows": [{"transaction_date": "2026-09-16", "side": "ALIS", "nominal": 10_000, "before": None, "after": "2.03"}]}
    public_adapter._price_rows(one_day)
    assert one_day["rows"] == [{"transaction_date": "2026-09-16", "side": "ALIS", "nominal": 10_000, "price": None, "price_low": None, "price_high": None, "post_pct_stake": "2.03"}]  # unpriced, not 10.000 × 1,63
    assert public_adapter.party_from_prose("Şirketimizin ana ortağı Beşiktaş Jimnastik Kulübü tarafından 37.450.000 adet BJKAS payı satılmıştır.") == ("Beşiktaş Jimnastik Kulübü", "ana ortağı", False)
    assert public_adapter.party_from_prose("1.000.000 TL toplam nominal tutarlı alış işlemi SMART HOLDİNG A.Ş. tarafından gerçekleştirilmiştir.") == ("SMART HOLDİNG A.Ş.", None, False)
    assert public_adapter.party_from_prose("750.000 TL toplam nominal tutarlı alış işlemi Şirketimizce gerçekleştirilmiştir.") == (None, None, True)
    assert public_adapter.party_from_prose("Şirketimiz İmtiyazlı pay sahibi AG Girişim Holding A.Ş.'den gelen yazı aşağıdaki gibidir: alış işlemi tarafımızca gerçekleştirilmiştir.") == ("AG Girişim Holding A.Ş.", "İmtiyazlı pay sahibi", False)
    # The issuer's own "Şirketimiz tarafından" is the issuer, never a party named "Şirketimiz"; a title without "Sayın" stays a title, not part of the name;
    # a regulator's "tarafından yayımlanan" names nobody, and the party is the person further on; "Sn." leaves no stray dot in the role.
    assert public_adapter.party_from_prose("1.000.000 TL toplam nominal tutarlı alış işlemi Şirketimiz tarafından gerçekleştirilmiştir.") == (None, None, True)
    assert public_adapter.party_from_prose("ŞİRKETİMİZ tarafından 100.000 adet alış işlemi gerçekleştirilmiştir.") == (None, None, True)
    assert public_adapter.party_from_prose("Şirketimiz Yönetim Kurulu Başkanı Ahmet Yılmaz tarafından 16.09.2026 tarihinde 10.000 adet alış işlemi gerçekleştirilmiştir.") == ("Ahmet Yılmaz", "Yönetim Kurulu Başkanı", False)
    assert public_adapter.party_from_prose("Sermaye Piyasası Kurulu tarafından yayımlanan II-15.1 sayılı tebliğ kapsamında Şirketimiz Yönetim Kurulu Üyesi Sayın Ali Veli tarafından 16.09.2026 tarihinde alış işlemi yapılmıştır.") == ("Ali Veli", "Yönetim Kurulu Üyesi", False)
    assert public_adapter.party_from_prose("Merkezi Kayıt Kuruluşu tarafından Şirketimize iletilen bilgiye göre Borsa İstanbul tarafından belirlenen kurallar çerçevesinde Yönetim Kurulu Üyemiz Ali Veli tarafından alış yapılmıştır.") == ("Ali Veli", "Yönetim Kurulu Üyemiz", False)
    assert public_adapter.party_from_prose("Sermaye Piyasası Kurulu tarafından yayımlanan tebliğ uyarınca açıklama yapılmıştır.") == (None, None, False)  # no regulator is ever a party
    assert public_adapter.party_from_prose("Yönetim Kurulu Başkanımız Sn. Ahmet Yılmaz tarafından 16.09.2026 tarihinde alış yapılmıştır.") == ("Ahmet Yılmaz", "Yönetim Kurulu Başkanımız", False)
    assert public_adapter.party_from_prose("alış işlemi Akfen Gayrimenkul Yatırım Ortaklığı A.Ş. tarafından gerçekleştirilmiştir.") == ("Akfen Gayrimenkul Yatırım Ortaklığı A.Ş.", None, False)  # a legal form is not a title
    assert [party_kind(n) for n in ("Mehmet Sönmez", "Akkök Holding A.Ş.", "Delta Global AG", "Rota Portföy Abis Hisse Senedi Serbest Özel Fonu", "Levent Sadık Amet (Sadık Ahmet)", "X")] == ["person", "company", "company", "fund", "person", "other"]
    # The issuer however its legal form is printed — the prose drops "A.Ş.", the header keeps it — and never two different companies.
    assert public_adapter._same_name("ATLANTİS YATIRIM HOLDİNG", "ATLANTİS YATIRIM HOLDİNG A.Ş.") and public_adapter._same_name("Atlantis Yatırım Holding AŞ", "ATLANTİS YATIRIM HOLDİNG A.Ş.")
    assert not public_adapter._same_name("Akkök Holding A.Ş.", "AKSA AKRİLİK KİMYA SANAYİİ A.Ş.") and not public_adapter._same_name("A.Ş.", "A.Ş.")
    own_page = _page(ATSYH_BUY).replace("Sayın Süleyman Yıldırım tarafından", "ATLANTİS YATIRIM HOLDİNG tarafından")
    assert (parse_insider_page(own_page).payload["party_name"], parse_insider_page(own_page).payload["party_is_issuer"]) == ("ATLANTİS YATIRIM HOLDİNG", True)


def test_normaliser_maps_turkish_titles_and_keys_parties_by_name():
    sale, buy, chair, entity = (parse_insider_filing(_parse(i)) for i in (GIPTA_SALE, BURVA_BUY, ATSYH_BUY, AKSA_BUY))
    assert (sale.party_name, sale.party_kind, sale.roles, sale.title, sale.is_issuer, sale.confidence) == ("Mehmet Sönmez", "person", "officer", "GENEL MÜDÜR", False, "EXACT")
    (row,) = sale.rows
    assert (row.transaction_date, row.code, row.nominal, row.price, row.price_low, row.post_pct_stake) == (date(2026, 8, 25), "S", 60_000, Decimal("103.2000"), None, Decimal("0.0758"))
    assert (buy.roles, buy.title, buy.rows[0].code, buy.rows[0].price, buy.rows[0].price_low, buy.rows[0].price_high) == ("director,shareholder", "Yönetim Kurulu Başkanı", "P", None, Decimal(594), Decimal(600))  # 55,14 %: a shareholder too
    assert (chair.roles, chair.title, chair.rows[0].code, chair.post_pct_stake) == ("director", "Yönetim Kurulu Başkanı", "P", Decimal("2.0379"))  # 2,04 %: below the 5 % line
    assert (entity.party_kind, entity.roles, entity.title, entity.rows[0].nominal) == ("company", "shareholder", None, 1_750_000)  # 40,22 %; the signatory's job is not a title
    assert sale.party_key == party_key("MEHMET SÖNMEZ") == party_key("Mehmet  Sönmez") and sale.party_key.startswith("k") and len(sale.party_key) == 10
    assert len({sale.party_key, buy.party_key, chair.party_key, entity.party_key}) == 4
    assert roles_from_title("Yönetim Kurulu Başkan Yardımcısı") == ["director"] and roles_from_title("Yönetim Kurulu Üyesi ve Genel Müdür") == ["director", "officer"]
    assert roles_from_title("YÖNETİM KURULU BAŞKANI") == ["director"] and roles_from_title("İcra Kurulu Başkanı (CEO)") == ["officer"] and roles_from_title("Mali İşler Direktörü") == ["officer"]
    assert roles_from_title("İmtiyazlı pay sahibi") == ["shareholder"] and roles_from_title("ana ortağı") == ["shareholder"] and roles_from_title("") == [] and roles_from_title(None) == []
    with pytest.raises(ValueError):  # a page whose numbers could not be read never becomes rows
        parse_insider_filing(parse_insider_page(_page(BURVA_BUY)))


# --- refresh (write path) ------------------------------------------------------------------------------------------


def test_refresh_stores_the_filings_once_and_never_touches_the_pys_path(session, caplog, monkeypatch):
    monkeypatch.setattr(public_adapter.time, "sleep", lambda _s: None)  # the adapter's retry backoff on the 404 below
    kap = _Kap()
    adapter = kap.adapter(pys_only=False)
    assert [r["disclosureIndex"] for r in adapter.list_insider_disclosures(date(2026, 8, 1), AS_OF)] == [1654800, 1662850, 1664326, 1664407]
    assert [r["disclosureIndex"] for r in kap.adapter().list_disclosures(date(2026, 8, 1), AS_OF)] == [1662606, 1662620]  # the PYŞ list: the other half, byte for byte the old filter
    kap.requests.clear()
    out = insiders.refresh_kap(session, adapter)
    assert out == {"listed": 4, "disclosures": 4, "transactions": 4, "skipped": 0}
    assert kap.requests.count("/tr/api/disclosure/members/byCriteria") == 1 and [p for p in kap.requests if p.startswith("/tr/Bildirim/")] == [f"/tr/Bildirim/{i}" for i in (GIPTA_SALE, BURVA_BUY, ATSYH_BUY, AKSA_BUY)]
    assert sorted(p.rsplit("/", 1)[-1] for p in kap.requests if "/file/download/" in p) == sorted(PDFS)  # one PDF per relayed filing, none for the issuer page

    discs = {d.source_id: d for d in session.scalars(select(Disclosure))}
    assert set(discs) == {GIPTA_SALE, BURVA_BUY, ATSYH_BUY, AKSA_BUY}
    assert all(d.kind == "KAP_INSIDER_TRANSACTION" and d.source == "KAP" and d.market_code == "TR" and d.parse_status == "PARSED" and d.parsed_at for d in discs.values())
    assert discs[BURVA_BUY].raw_uri == f"https://www.kap.org.tr/tr/Bildirim/{BURVA_BUY}" and discs[BURVA_BUY].payload["party_name"] == "Ümit Gümüş" and discs[BURVA_BUY].published_at == datetime(2026, 9, 15, 13, 23, 10)
    symbols = {i.symbol: i for i in session.scalars(select(Instrument).where(Instrument.market_code == "TR"))}
    assert set(symbols) == {"GIPTA", "BURVA", "ATSYH", "AKSA"} and symbols["GIPTA"].name.startswith("GIPTA OFİS") and symbols["ATSYH"].name == "ATSYH" and not symbols["BURVA"].is_verified

    rows = {r.kap_disclosure_index: r for r in session.scalars(select(InsiderTransaction))}
    assert set(rows) == {1654800, 1662850, 1664326, 1664407} and len({r.row_hash for r in rows.values()}) == 4
    sale = rows[1654800]
    assert (sale.instrument_id, sale.insider_name, sale.insider_cik, sale.roles, sale.title) == (symbols["GIPTA"].id, "Mehmet Sönmez", party_key("Mehmet Sönmez"), "officer", "GENEL MÜDÜR")
    assert (sale.transaction_date, sale.filed_at, sale.code, sale.acquired, sale.shares, sale.price, sale.post_shares) == (date(2026, 8, 25), datetime(2026, 8, 25, 14, 52, 55), "S", False, Decimal(60_000), Decimal("103.2000"), None)
    assert (sale.ownership, sale.derivative, sale.is_superseded, sale.confidence, sale.party_kind, sale.post_pct_stake, sale.disclosure_id) == ("D", False, False, "EXACT", "person", Decimal("0.0758"), discs[GIPTA_SALE].id)
    buy = rows[1662850]
    assert (buy.code, buy.acquired, buy.shares, buy.price, buy.roles, buy.party_kind, buy.post_pct_stake) == ("P", True, Decimal(30_000), None, "director,shareholder", "person", Decimal("55.14"))
    assert (rows[1664326].roles, rows[1664326].title, rows[1664326].post_pct_stake) == ("director", "Yönetim Kurulu Başkanı", Decimal("2.0379"))
    assert (rows[1664407].party_kind, rows[1664407].roles, rows[1664407].title, rows[1664407].insider_name) == ("company", "shareholder", None, "Akkök Holding A.Ş.")
    assert session.scalar(select(TransactionEvent.id)) is None  # law: insider rows never enter transaction_events

    # A re-run lists and fetches nothing new: every index is known, whichever path stored it.
    kap.requests.clear()
    assert insiders.refresh_kap(session, kap.adapter(pys_only=False)) == {"listed": 4, "disclosures": 0, "transactions": 0, "skipped": 0}
    assert kap.requests == ["/tr/api/disclosure/members/byCriteria"] and len(session.scalars(select(InsiderTransaction)).all()) == 4
    # The PYŞ ingest over the same site stores its own two filings, parses them into transaction events and never
    # sees the insider ones (nor does the insider path re-fetch the PYŞ pages afterwards).
    assert pipeline.ingest(session, kap.adapter()) == 2 and pipeline.parse_pending(session) == 2
    assert {d.kind for d in session.scalars(select(Disclosure))} == {"KAP_SHARE_TRANSACTION", "KAP_INSIDER_TRANSACTION"} and len(session.scalars(select(TransactionEvent)).all()) == 2
    assert len(session.scalars(select(Disclosure)).all()) == 6 and insiders.refresh_kap(session, kap.adapter(pys_only=False))["disclosures"] == 0

    # A page the site cannot serve is skipped by the adapter and retried next run; one the parser cannot read is kept
    # FAILED with the reason, never fetched again, and never gets rows.
    kap.listing.append({**kap.listing[0], "disclosureIndex": 1664999, "publishDate": "17.09.2026 10:00:00"})
    with caplog.at_level("WARNING", logger="instilens.kap"):
        assert insiders.refresh_kap(session, kap.adapter(pys_only=False)) == {"listed": 5, "disclosures": 0, "transactions": 0, "skipped": 1}
    assert any("kap insider 1664999 skipped" in r.getMessage() for r in caplog.records) and session.scalar(select(Disclosure.id).where(Disclosure.source_id == "1664999")) is None
    # The attachment is served but is not the form (a fund's portfolio report): no party fields, no rows.
    kap.pages["1664999"] = _page(BURVA_BUY).replace(BURVA_BUY, "1664999").replace("4028328da09bf08e01a0a494fe4f4031", "0000000000000000000000000000beef")
    kap.pdfs["0000000000000000000000000000beef"] = "VPS_pdr_2026w36.pdf"
    caplog.clear()
    with caplog.at_level("WARNING", logger="instilens.insiders"):
        assert insiders.refresh_kap(session, kap.adapter(pys_only=False)) == {"listed": 5, "disclosures": 0, "transactions": 0, "skipped": 1}
    failed = session.scalar(select(Disclosure).where(Disclosure.source_id == "1664999"))
    assert failed.parse_status == "FAILED" and "rows" in failed.parse_error and failed.payload["party_name"] == "Ümit Gümüş"
    assert any("KAP 1664999" in r.getMessage() and "kept without rows" in r.getMessage() for r in caplog.records)
    kap.requests.clear()
    assert insiders.refresh_kap(session, kap.adapter(pys_only=False))["skipped"] == 0 and kap.requests == ["/tr/api/disclosure/members/byCriteria"]
    assert len(session.scalars(select(InsiderTransaction)).all()) == 4
    # The attachment the site serves under a .pdf objId is not a PDF at all (an HTML error page): pypdf's error ends
    # that page, not the run — it is kept FAILED with the attachment error, and never fetched again.
    kap.pages["1665000"] = _page(BURVA_BUY).replace(BURVA_BUY, "1665000").replace("4028328da09bf08e01a0a494fe4f4031", "0000000000000000000000000000dead")
    kap.pdfs["0000000000000000000000000000dead"] = "1662850.html"
    kap.listing.append({**kap.listing[0], "disclosureIndex": 1665000, "publishDate": "17.09.2026 11:00:00"})
    caplog.clear()
    with caplog.at_level("WARNING", logger="instilens.kap"):
        assert insiders.refresh_kap(session, kap.adapter(pys_only=False)) == {"listed": 6, "disclosures": 0, "transactions": 0, "skipped": 1}
    assert any("kap insider 1665000: attachment" in r.getMessage() and "not a readable PDF" in r.getMessage() for r in caplog.records)
    bad_pdf = session.scalar(select(Disclosure).where(Disclosure.source_id == "1665000"))
    assert bad_pdf.parse_status == "FAILED" and bad_pdf.payload["attachment_error"].startswith("PdfStreamError") and bad_pdf.payload["rows"] == []
    kap.requests.clear()
    assert insiders.refresh_kap(session, kap.adapter(pys_only=False))["skipped"] == 0 and kap.requests == ["/tr/api/disclosure/members/byCriteria"]
    assert len(session.scalars(select(InsiderTransaction)).all()) == 4


def _replay(index: str, *, as_index: str, amends: str | None = None) -> str:
    """The real page under another index — the way test_insiders replays a real Form 4 under a 4/A listing entry;
    `amends` fills the RSC `relatedDisclosureIndex` the site carries beside `disclosureBasic` (null on the originals)."""
    page = _page(index).replace(index, as_index)
    if amends:
        assert page.count('relatedDisclosureIndex\\":null') == 1
        page = page.replace('relatedDisclosureIndex\\":null', f'relatedDisclosureIndex\\":{amends}')
    return page


def _corrected_form(text: str) -> str:
    """The real form's text with its "Yapılan Açıklama Düzeltme mi?" answer flipped to EVET."""
    flipped = re.sub(r"(Düzeltme mi\?\s*:\s*)HAYIR", r"\1EVET", text)
    assert flipped != text
    return flipped


def test_a_correction_supersedes_the_filing_it_names_or_the_partys_latest(session, monkeypatch):
    kap = _Kap()
    insiders.refresh_kap(session, kap.adapter(pys_only=False))
    original = session.scalar(select(Disclosure).where(Disclosure.source_id == GIPTA_SALE))
    gipta = session.scalar(select(Instrument).where(Instrument.symbol == "GIPTA"))
    assert insiders.summary(session, gipta.id, 60, AS_OF)["open_market_sells"] == 1

    # The same filing again as a "Düzeltme" naming no index (the relayed form's flag, read from the PDF): it replaces the
    # party's latest live filing on the stock. The old rows stay, flagged.
    real_pdf_text = public_adapter.pdf_text
    monkeypatch.setattr(public_adapter, "pdf_text", lambda blob: _corrected_form(real_pdf_text(blob)))
    kap.pages["1654900"] = _replay(GIPTA_SALE, as_index="1654900")
    kap.listing.append({**kap.listing[0], "disclosureIndex": 1654900, "publishDate": "26.08.2026 09:00:00"})
    assert insiders.refresh_kap(session, kap.adapter(pys_only=False)) == {"listed": 5, "disclosures": 1, "transactions": 1, "skipped": 0}
    fix = session.scalar(select(Disclosure).where(Disclosure.source_id == "1654900"))
    assert fix.payload["is_correction"] is True and fix.payload["amends_source_id"] is None and fix.supersedes_id == original.id and original.is_superseded
    rows = {r.disclosure_id: r for r in session.scalars(select(InsiderTransaction).where(InsiderTransaction.instrument_id == gipta.id))}
    assert rows[original.id].is_superseded and not rows[fix.id].is_superseded and rows[fix.id].row_hash != rows[original.id].row_hash
    s = insiders.summary(session, gipta.id, 60, AS_OF)
    assert (s["sellers"], s["open_market_sells"], s["sell_value"]) == (1, 1, float(Decimal(60_000) * Decimal("103.2000")))
    (tx,) = insiders.transactions(session, gipta.id, 60, as_of=AS_OF)
    assert tx["accession"] == "1654900" and tx["url"].endswith("/tr/Bildirim/1654900")

    # A correction that names the disclosure it amends (the site's relatedDisclosureIndex) replaces exactly that one —
    # here the first correction — never the party's other filings.
    kap.pages["1654950"] = _replay(GIPTA_SALE, as_index="1654950", amends="1654900")
    kap.listing.append({**kap.listing[0], "disclosureIndex": 1654950, "publishDate": "27.08.2026 09:00:00"})
    assert insiders.refresh_kap(session, kap.adapter(pys_only=False))["disclosures"] == 1
    second = session.scalar(select(Disclosure).where(Disclosure.source_id == "1654950"))
    assert second.payload["amends_source_id"] == "1654900" and second.supersedes_id == fix.id and fix.is_superseded
    assert [d.source_id for d in session.scalars(select(Disclosure).where(Disclosure.is_superseded.is_(False), Disclosure.kind == "KAP_INSIDER_TRANSACTION")).all() if d.payload["subject_symbol"] == "GIPTA"] == ["1654950"]
    assert insiders.summary(session, gipta.id, 60, AS_OF)["open_market_sells"] == 1
    monkeypatch.setattr(public_adapter, "pdf_text", real_pdf_text)

    # An original stored after its correction (the page could not be read the first time) is flagged on arrival by the
    # correction that names it.
    kap.pages["1654700"] = _replay(GIPTA_SALE, as_index="1654700")
    kap.pages["1654760"] = _replay(GIPTA_SALE, as_index="1654760", amends="1654700")
    kap.listing += [{**kap.listing[0], "disclosureIndex": 1654760, "publishDate": "24.08.2026 12:00:00"}]
    monkeypatch.setattr(public_adapter, "pdf_text", lambda blob: _corrected_form(real_pdf_text(blob)))
    assert insiders.refresh_kap(session, kap.adapter(pys_only=False))["disclosures"] == 1
    monkeypatch.setattr(public_adapter, "pdf_text", real_pdf_text)
    late_fix = session.scalar(select(Disclosure).where(Disclosure.source_id == "1654760"))
    assert late_fix.payload["is_correction"] and late_fix.supersedes_id is None  # its original is not there yet
    kap.listing.append({**kap.listing[0], "disclosureIndex": 1654700, "publishDate": "24.08.2026 10:00:00"})
    assert insiders.refresh_kap(session, kap.adapter(pys_only=False))["disclosures"] == 1
    late = session.scalar(select(Disclosure).where(Disclosure.source_id == "1654700"))
    assert late.is_superseded and late_fix.supersedes_id == late.id
    assert insiders.summary(session, gipta.id, 60, AS_OF)["open_market_sells"] == 2  # two live filings: 1654950 and 1654760 (each one sale)


def test_a_correction_on_an_issuer_page_uses_the_pages_own_field(session):
    """The issuer's ODA page carries "Yapılan Açıklama Düzeltme mi?" itself (the same field the PYŞ parser reads)."""
    kap = _Kap()
    insiders.refresh_kap(session, kap.adapter(pys_only=False))
    original = session.scalar(select(Disclosure).where(Disclosure.source_id == ATSYH_BUY))
    page = _page(ATSYH_BUY).replace(ATSYH_BUY, "1664399")
    i = page.find("Correction Notification Flag")  # the two answer cells (TR, EN) that follow the field's label
    page = page[:i] + page[i:].replace("Hayır (No)", "Evet (Yes)", 2)
    raw = parse_insider_page(page)
    assert raw.payload["is_correction"] is True and raw.payload["amends_source_id"] is None
    assert insiders._store_kap_filing(session, raw) == 1
    fix = session.scalar(select(Disclosure).where(Disclosure.source_id == "1664399"))
    assert fix.supersedes_id == original.id and original.is_superseded
    atsyh = session.scalar(select(Instrument).where(Instrument.symbol == "ATSYH"))
    assert insiders.summary(session, atsyh.id, 60, AS_OF)["buyers"] == 1 and [t["accession"] for t in insiders.transactions(session, atsyh.id, 60, as_of=AS_OF)] == ["1664399"]


# --- read models: buybacks, the route -------------------------------------------------------------------------------


def _own_shares(session, index: str, side: str = "ALIS") -> Disclosure:
    """The issuer trading its own shares, derived from the real Atlantis page: the same numbers with the party set
    to the company itself (no BIST issuer filed a buyback under this subject in the windows read — they use another
    subject — so the rule is exercised on the real page's payload with the party swapped)."""
    raw = _parse(ATSYH_BUY)
    payload = {**raw.payload, "party_name": raw.payload["member_name"], "party_kind": "company", "party_is_issuer": True, "role_text": None, "summary": "Pay Geri Alım İşlemi"}
    payload["rows"] = [{**r, "side": side} for r in payload["rows"]]
    replayed = raw.model_copy(update={"source_id": index, "raw_uri": f"{BASE}/tr/Bildirim/{index}", "payload": payload})
    assert insiders._store_kap_filing(session, replayed) == 1
    return session.scalar(select(Disclosure).where(Disclosure.source_id == index))


def test_buybacks_are_listed_with_their_label_but_never_counted(session):
    kap = _Kap()
    insiders.refresh_kap(session, kap.adapter(pys_only=False))
    atsyh = session.scalar(select(Instrument).where(Instrument.symbol == "ATSYH"))
    _own_shares(session, "1664350")
    _own_shares(session, "1664351", side="SATIS")
    rows = session.scalars(select(InsiderTransaction).where(InsiderTransaction.instrument_id == atsyh.id).order_by(InsiderTransaction.id)).all()
    assert [(r.code, r.roles, r.party_kind, r.insider_name) for r in rows] == [("P", "director", "person", "Süleyman Yıldırım"), ("P", ISSUER_ROLE, "company", "ATLANTİS YATIRIM HOLDİNG A.Ş."), ("S", ISSUER_ROLE, "company", "ATLANTİS YATIRIM HOLDİNG A.Ş.")]
    # Listed — with the label the UI renders as "Şirket geri alımı" — but neither a buyer nor a seller, nor a cluster member.
    listed = insiders.transactions(session, atsyh.id, 60, as_of=AS_OF)
    assert [(t["code"], t["buyback"], t["party_kind"], t["role"]) for t in listed] == [("P", False, "person", "director"), ("P", True, "company", "issuer"), ("S", True, "company", "issuer")]
    s = insiders.summary(session, atsyh.id, 60, AS_OF)
    assert (s["buyers"], s["sellers"], s["open_market_buys"], s["open_market_sells"], s["buy_value"], s["cluster"]) == (1, 0, 1, 0, 0.0, None)  # the range-only purchase is unpriced
    assert [t.insider_name for t in insiders.trades(session, atsyh.id, AS_OF - timedelta(days=60), AS_OF)] == ["Süleyman Yıldırım"]
    _insider_rows(session, atsyh, session.scalar(select(Disclosure).where(Disclosure.source_id == ATSYH_BUY)), ("k000000002", "İkinci Üye", date(2026, 9, 10), "P"))
    assert insiders.detected_signals(session, AS_OF) == []  # two persons + the company: no cluster
    s = insiders.stock_insiders(session, "TR", "ATSYH", 60, AS_OF)["summary"]
    assert (s["buyers"], s["open_market_buys"], s["cluster"]) == (2, 2, None)


def _insider_rows(session, inst: Instrument, disc: Disclosure, *rows: tuple[str, str, date, str]) -> None:
    """Extra KAP rows on an existing disclosure (the pattern test_insiders uses on a real Form 4): (party key, name, date, code)."""
    for i, (key, name, day, code) in enumerate(rows):
        session.add(InsiderTransaction(
            disclosure_id=disc.id, instrument_id=inst.id, insider_cik=key, insider_name=name, roles="director", title=None, transaction_date=day,
            filed_at=datetime.combine(day, datetime.min.time()), code=code, acquired=code == "P", shares=Decimal(100), price=Decimal("50"), post_shares=None,
            ownership="D", derivative=False, row_hash=f"test-{key}-{i}-{day}-{code}", kap_disclosure_index=int(disc.source_id), party_kind="person",
        ))
    session.flush()


def _client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    token = auth.issue_token(auth.register(session, "k@example.com", "correct-horse-1", "K"))
    c = TestClient(app)
    c.headers["authorization"] = f"Bearer {token}"
    return c, app


def test_tr_route_has_the_us_shape_with_kap_rows(session, monkeypatch):
    kap = _Kap()
    c, app = _client(session)
    try:
        before = c.get("/api/v1/stocks/BURVA/insiders")
        assert before.status_code == 404  # the symbol is created by the first filing that names it
        insiders.refresh_kap(session, kap.adapter(pys_only=False))
        body = c.get("/api/v1/stocks/burva/insiders", params={"days": 730}).json()
        assert set(body) == {"symbol", "name", "market", "supported", "days", "as_of", "source", "fetched_at", "coverage_since", "summary", "transactions", "truncated", "edgar_url", "more_url"}
        assert body["symbol"] == "BURVA" and body["market"] == "TR" and body["supported"] is True and body["source"] == "kap" and body["edgar_url"] is None and body["truncated"] is False
        assert datetime.fromisoformat(body["fetched_at"]) and body["name"] == "BURÇELİK VANA SANAYİ VE TİCARET A.Ş."
        assert body["coverage_since"] == "2026-08-25"  # the feed's oldest stored filing (GIPTA): the window is only read from there
        assert body["more_url"] is None  # BURVA's filing was relayed by MKK: the issuer's own KAP page is not known from it
        assert set(body["summary"]) == {"buyers", "sellers", "buy_value", "sell_value", "net_value", "open_market_buys", "open_market_sells", "cluster"}
        (tx,) = body["transactions"]
        assert set(tx) == {"id", "transaction_date", "filed_at", "insider", "insider_cik", "role", "title", "code", "acquired", "shares", "price", "price_range", "value",
                           "post_shares", "post_pct_stake", "ownership", "derivative", "party_kind", "buyback", "confidence", "accession", "url"}
        assert tx == {"id": tx["id"], "transaction_date": "2026-09-15", "filed_at": "2026-09-15T13:23:10", "insider": "Ümit Gümüş", "insider_cik": party_key("Ümit Gümüş"), "role": "director,shareholder",
                      "title": "Yönetim Kurulu Başkanı", "code": "P", "acquired": True, "shares": 30000.0, "price": None, "price_range": [594.0, 600.0], "value": None, "post_shares": None,
                      "post_pct_stake": 55.14, "ownership": "D", "derivative": False, "party_kind": "person", "buyback": False, "confidence": "EXACT", "accession": BURVA_BUY,
                      "url": f"https://www.kap.org.tr/tr/Bildirim/{BURVA_BUY}"}
        gipta = c.get("/api/v1/stocks/GIPTA/insiders", params={"days": 730}).json()
        assert (gipta["summary"]["sellers"], gipta["summary"]["open_market_sells"], gipta["summary"]["sell_value"], gipta["summary"]["net_value"]) == (1, 1, 6_192_000.0, -6_192_000.0)
        assert gipta["transactions"][0]["price"] == 103.2 and gipta["transactions"][0]["price_range"] is None and gipta["transactions"][0]["value"] == 6_192_000.0
        aksa = c.get("/api/v1/stocks/AKSA/insiders", params={"days": 730}).json()["transactions"][0]
        assert (aksa["party_kind"], aksa["role"], aksa["title"], aksa["insider"], aksa["post_pct_stake"]) == ("company", "shareholder", None, "Akkök Holding A.Ş.", 40.22)
        atsyh = c.get("/api/v1/stocks/ATSYH/insiders", params={"days": 730}).json()
        assert atsyh["more_url"] == "https://www.kap.org.tr/tr/sirket-bilgileri/ozet/4028e4a241558bd9014155dde60a05e7"  # the issuer's own page carries its oid
        # A BIST symbol without filings after the feed has run: supported, empty, fetched_at set (the feed did run).
        from instilens.services.entities import EntityResolver

        EntityResolver(session).instrument(Market.TR, "ASELS")
        empty = c.get("/api/v1/stocks/ASELS/insiders").json()
        assert empty["supported"] is True and empty["fetched_at"] is not None and empty["transactions"] == [] and empty["summary"]["buyers"] == 0
        assert empty["coverage_since"] == "2026-08-25" and empty["more_url"] is None  # "nothing since 25 Aug", never "nothing in 90 days"
        assert c.get("/api/v1/stocks/ASELS/filings").json()["supported"] is False  # the EDGAR filings index stays US-only
        # stock_detail's block and the freshness row exist for BIST once the feed has run.
        detail = insiders.detail(session, session.scalar(select(Instrument).where(Instrument.symbol == "BURVA")), AS_OF)
        assert detail == {"days": 90, "buyers": 1, "sellers": 0, "net_value": 0.0, "cluster": False}
        assert insiders.last_filed_at(session, "TR") == datetime(2026, 9, 17, 9, 22, 17) and insiders.last_filed_at(session, "US") is None
        assert analytics.stock_detail(session, "TR", "BURVA")["insiders"]["days"] == 90
        assert analytics.data_freshness(session, "TR")[2] == {"source": "KAP insider filings", "cadence": "Every 30 min on trading days", "last": "2026-09-17", "delayed": False}  # the trust bar names the feed
        monkeypatch.setattr(insiders, "MAX_TRANSACTIONS", 1)
        _insider_rows(session, session.scalar(select(Instrument).where(Instrument.symbol == "BURVA")), session.scalar(select(Disclosure).where(Disclosure.source_id == BURVA_BUY)), ("k000000009", "Bir Üye", date(2026, 9, 12), "P"))
        capped = c.get("/api/v1/stocks/BURVA/insiders", params={"days": 730}).json()
        assert capped["truncated"] is True and len(capped["transactions"]) == 1 and capped["summary"]["buyers"] == 2
    finally:
        app.dependency_overrides.clear()


# --- cluster + alert on a BIST watchlist ---------------------------------------------------------------------------


def test_cluster_and_alert_fire_on_a_bist_watchlist(session):
    kap = _Kap()
    insiders.refresh_kap(session, kap.adapter(pys_only=False))
    burva = session.scalar(select(Instrument).where(Instrument.symbol == "BURVA"))
    disc = session.scalar(select(Disclosure).where(Disclosure.source_id == BURVA_BUY))
    watchlist = Watchlist(owner_id="1", name="w")
    session.add(watchlist)
    session.flush()
    session.add(WatchlistItem(watchlist_id=watchlist.id, instrument_id=burva.id))
    session.flush()
    _insider_rows(session, burva, disc, ("k000000002", "İkinci Üye", date(2026, 9, 10), "P"))
    pipeline.compute_intelligence(session, AS_OF)
    assert session.scalar(select(Signal).where(Signal.signal_type == SignalType.INSIDER_BUY_CLUSTER)) is None  # two persons
    _insider_rows(session, burva, disc, ("k000000003", "Üçüncü Üye", date(2026, 9, 18), "P"), ("k000000004", "Şirket", date(2026, 9, 18), "P"))
    session.scalar(select(InsiderTransaction).where(InsiderTransaction.insider_cik == "k000000004")).roles = ISSUER_ROLE  # the company's own purchase never counts
    session.flush()
    assert insiders.detected_signals(session, date(2026, 9, 9)) == []  # before the window holds three
    pipeline.compute_intelligence(session, AS_OF)
    (sig,) = session.scalars(select(Signal).where(Signal.signal_type == SignalType.INSIDER_BUY_CLUSTER)).all()
    assert sig.instrument_id == burva.id and sig.market_code == "TR" and sig.confidence == "EXACT" and (sig.window_start, sig.window_end) == (date(2026, 9, 10), AS_OF)
    assert sig.evidence["insiders"] == 3 and sig.evidence["names"] == ["İkinci Üye", "Üçüncü Üye", "Ümit Gümüş"] and BURVA_BUY in sig.evidence["accessions"]
    assert sig.evidence["unpriced"] == 1 and Decimal(sig.evidence["value"]) == 2 * Decimal(100) * Decimal(50)  # the range-only purchase adds breadth, not value
    assert alerts.evaluate(session, AS_OF) == 1
    (note,) = session.scalars(select(Notification)).all()
    assert note.dedup_key.startswith("wl:") and ":INSIDER_BUY_CLUSTER:cluster:" in note.dedup_key and note.link == "/stocks/BURVA"
    assert note.title == "BURVA: 3 şirket içi kişi son 30 günde pay aldı (KAP)" and "Ümit Gümüş" in note.body and "₺10,000 (KAP)" in note.body and "2026-09-10" in note.body  # no venue is stated on KAP: never "açık piyasadan"
    assert "$" not in note.body and "Form 4" not in note.body and "açık piyasa" not in note.title
    assert alerts.evaluate(session, AS_OF) == 0  # once per episode
    # An explicit INSIDER_BUY_CLUSTER rule of an English-language user: the same KAP wording in English.
    from instilens.domain.models import AlertRule, User

    session.add(User(email="e@example.com", password_hash="x", name="E", lang="en"))
    session.flush()
    session.add(AlertRule(owner_id=str(session.scalar(select(User.id).where(User.email == "e@example.com"))), instrument_id=burva.id, rule_type="INSIDER_BUY_CLUSTER", params={}))
    session.flush()
    assert alerts.evaluate(session, AS_OF) == 1
    (en,) = session.scalars(select(Notification).where(Notification.alert_rule_id.is_not(None))).all()
    assert en.title == "BURVA: 3 insiders bought shares in the last 30 days (KAP)" and "open market" not in en.title
    assert alerts.evaluate(session, AS_OF) == 0  # once per episode
    assert analytics.stock_detail(session, "TR", "BURVA")["signals"][0]["type"] == "INSIDER_BUY_CLUSTER"


# --- settings, scheduler, migration -------------------------------------------------------------------------------


def test_scheduler_job_is_gated_and_recomputes_after_new_rows(monkeypatch):
    import contextlib

    from instilens import scheduler
    from instilens.services import runtime_settings

    calls: list[str] = []
    results = iter([{"listed": 3, "disclosures": 1, "transactions": 0, "skipped": 1}, {"listed": 3, "disclosures": 1, "transactions": 2, "skipped": 0}])
    monkeypatch.setattr(insiders, "refresh_kap", lambda s: calls.append("refresh") or next(results))
    monkeypatch.setattr(scheduler, "compute", lambda: calls.append("compute"))
    monkeypatch.setattr(scheduler, "session_scope", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(settings, "kap_adapter", "public")
    monkeypatch.setattr(settings, "kap_insiders_enabled", False)
    scheduler.kap_insiders()
    assert calls == []
    monkeypatch.setattr(settings, "kap_insiders_enabled", True)
    monkeypatch.setattr(settings, "kap_adapter", "fixture")  # only the public site carries the listing
    scheduler.kap_insiders()
    assert calls == []
    monkeypatch.setattr(settings, "kap_adapter", "public")
    scheduler.kap_insiders()
    assert calls == ["refresh"]  # rows: none → no recompute
    scheduler.kap_insiders()
    assert calls == ["refresh", "refresh", "compute"]
    assert runtime_settings.EDITABLE["kap_insiders_enabled"] == {"type": "bool", "group": "data"} and runtime_settings.coerce("kap_insiders_enabled", "off") is False
    assert runtime_settings.EDITABLE["kap_insiders_max_details"] == {"type": "int", "group": "data", "min": 1, "max": 500} and runtime_settings.coerce("kap_insiders_max_details", "80") == 80
    assert settings.kap_insiders_max_details == 60
    assert insiders.build_kap_client().max_details == 60 and insiders.build_kap_client().pys_only is False


def test_migration_adds_the_kap_columns_on_scratch_sqlite(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    from instilens.config import BACKEND_ROOT

    url = f"sqlite:///{tmp_path / 'scratch.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    cols = {c["name"]: c for c in inspect(eng).get_columns("insider_transactions")}
    assert {"kap_disclosure_index", "party_kind", "post_pct_stake"} <= set(cols) and all(cols[c]["nullable"] for c in ("kap_disclosure_index", "party_kind", "post_pct_stake"))
    assert str(cols["party_kind"]["type"]) == "VARCHAR(12)" and str(cols["post_pct_stake"]["type"]) == "NUMERIC(9, 4)" and str(cols["kap_disclosure_index"]["type"]) == "INTEGER"
    with eng.begin() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "b4c5d6e7f8a9"
    eng.dispose()
    command.downgrade(cfg, "a3b4c5d6e7f8")
    eng = create_engine(url)
    names = {c["name"] for c in inspect(eng).get_columns("insider_transactions")}
    assert {"kap_disclosure_index", "party_kind", "post_pct_stake"}.isdisjoint(names) and "row_hash" in names
    eng.dispose()
