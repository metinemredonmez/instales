"""Insiders (Faz 4): the Form 4 parser and the submissions listing on real EDGAR documents (fixtures/sec/form4), the
refresh loader behind an httpx MockTransport, 4/A supersession, cluster detection, episodic signals and the alert
rule, the routes, stock_detail, the AI tools, the migration, the issuer universe and CIK mapping. Network-free."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, select, text

from instilens.config import settings
from instilens.domain.enums import Market, SignalType
from instilens.domain.models import (
    Disclosure,
    InsiderTransaction,
    Instrument,
    Notification,
    PortfolioSnapshot,
    PositionChange,
    SecFiling,
    Signal,
    Watchlist,
    WatchlistItem,
)
from instilens.engine import insiders as engine
from instilens.ingestion.sec import tickers
from instilens.ingestion.sec.edgar_client import EdgarClient, parse_recent_filings
from instilens.ingestion.sec.form4 import (
    CODE_LABELS,
    officer_title,
    parse_form4,
    primary_owner,
    roles_of,
)
from instilens.services import alerts, analytics, auth, insiders, pipeline
from instilens.services.entities import EntityResolver
from tests.conftest import FIXTURES

FORM4 = FIXTURES / "sec" / "form4"
AAPL_SALE = "0001140361-26-036226"  # Apple, Newstead: S 1,438 @ 317.23 (2026-09-08, filed 2026-09-10)
AAPL_RSU = "0001140361-26-025622"  # Apple, Newstead: M (no price) + F @ 296.42 + derivative M (2026-06-15, filed 2026-06-17)
OXY_BUY = "0001628280-26-045313"  # Occidental, Jackson (CEO + director): P 4,770 @ 52.38 (2026-06-23, filed 2026-06-24)
DVA_JOINT = "0001193125-26-333151"  # DaVita, Berkshire Hathaway + Buffett (joint 10% owner filing): S 182,980 @ 199.548 (2026-07-31, filed 2026-08-04)
AS_OF = date(2026, 9, 17)  # the refresh reference day: 120 days back reaches 2026-05-20


def _xml(accession: str) -> str:
    return (FORM4 / f"{accession}.form4.xml").read_text(encoding="utf-8")


def _submissions(cik: str) -> dict:
    return json.loads((FORM4 / f"CIK{int(cik):010d}.submissions.json").read_text(encoding="utf-8"))


def _ticker_map() -> dict:
    return json.loads((FORM4 / "company_tickers.excerpt.json").read_text(encoding="utf-8"))


class _Edgar:
    """EDGAR behind httpx.MockTransport: the fixture documents by URL, everything else 404. `listings` and `documents`
    can be overridden per test; `status` forces a code for a URL fragment; `requests` records every call."""

    def __init__(self) -> None:
        self.listings = {"320193": _submissions("320193"), "797468": _submissions("797468")}
        self.documents = {
            f"/Archives/edgar/data/320193/{AAPL_SALE.replace('-', '')}/form4.xml": _xml(AAPL_SALE),
            f"/Archives/edgar/data/320193/{AAPL_RSU.replace('-', '')}/form4.xml": _xml(AAPL_RSU),
            f"/Archives/edgar/data/797468/{OXY_BUY.replace('-', '')}/wk-form4_1782342238.xml": _xml(OXY_BUY),
        }
        self.status: dict[str, int] = {}
        self.requests: list[str] = []
        self.client = EdgarClient("test", client=httpx.Client(transport=httpx.MockTransport(self)))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        url, path = str(request.url), request.url.path
        self.requests.append(url)
        for fragment, code in self.status.items():
            if fragment in url:
                return httpx.Response(code, request=request)
        if path == "/files/company_tickers.json":
            return httpx.Response(200, json=_ticker_map(), request=request)
        if path.startswith("/submissions/CIK"):
            cik = str(int(path[len("/submissions/CIK"):-len(".json")]))
            return httpx.Response(200, json=self.listings[cik], request=request) if cik in self.listings else httpx.Response(404, request=request)
        if path in self.documents:
            return httpx.Response(200, text=self.documents[path], request=request)
        return httpx.Response(404, request=request)


def _us(session, *symbols: str, watch: bool = True) -> list[Instrument]:
    """US instruments (on a watchlist by default, so `universe()` picks them up)."""
    resolver = EntityResolver(session)
    resolver.ensure_markets()
    out = [resolver.instrument(Market.US, s) for s in symbols]
    if watch:
        watchlist = Watchlist(owner_id="1", name="w")
        session.add(watchlist)
        session.flush()
        session.add_all([WatchlistItem(watchlist_id=watchlist.id, instrument_id=i.id) for i in out])
        session.flush()
    return out


# --- parser ---------------------------------------------------------------------------------------


def test_parse_form4_sale_purchase_and_rsu_settlement():
    sale = parse_form4(_xml(AAPL_SALE))
    assert sale["document_type"] == "4" and sale["period_of_report"] == "2026-09-08" and sale["date_of_original_submission"] is None
    assert sale["issuer"] == {"cik": "320193", "name": "Apple Inc.", "symbol": "AAPL"}
    (owner,) = sale["reporting_owners"]
    assert owner["cik"] == "1780525" and owner["name"] == "Newstead Jennifer" and owner["officer_title"] == "SVP, GC and Government Affairs"
    assert (owner["director"], owner["officer"], owner["ten_percent_owner"], owner["other"]) == (False, True, False, False) and roles_of(owner) == "officer"
    assert sale["aff_10b5_one"] is True and sale["derivative_transactions"] == []
    (row,) = sale["non_derivative_transactions"]
    assert row == {"security_title": "Common Stock", "date": "2026-09-08", "deemed_execution_date": None, "code": "S", "form_type": "4", "equity_swap_involved": False,
                   "acquired_disposed": "D", "shares": "1438", "price": "317.23", "post_shares": "34352", "ownership_nature": "D", "nature_of_ownership": None,
                   "footnote_ids": ["F1"], "derivative": False}
    assert sale["footnotes"]["F1"].startswith("This transaction was made pursuant to a Rule 10b5-1 trading plan")

    rsu = parse_form4(_xml(AAPL_RSU))
    m, f = rsu["non_derivative_transactions"]
    assert (m["code"], m["acquired_disposed"], m["shares"], m["price"], m["footnote_ids"]) == ("M", "A", "30104", None, ["F1"])  # price cell holds only a footnote → None, never 0
    assert (f["code"], f["acquired_disposed"], f["shares"], f["price"], f["post_shares"]) == ("F", "D", "16238", "296.42", "41546")
    (d,) = rsu["derivative_transactions"]
    assert d["derivative"] is True and d["security_title"] == "Restricted Stock Unit" and d["code"] == "M" and d["acquired_disposed"] == "D"
    assert (d["exercise_price"], d["exercise_date"], d["expiration_date"], d["underlying_title"], d["underlying_shares"], d["post_shares"]) == (None, None, None, "Common Stock", "30104", "210728")
    assert d["footnote_ids"] == ["F1", "F3"] and set(rsu["footnotes"]) == {"F1", "F2", "F3"} and "tax withholding" in rsu["footnotes"]["F2"]
    assert rsu["aff_10b5_one"] is False

    buy = parse_form4(_xml(OXY_BUY))
    assert buy["issuer"] == {"cik": "797468", "name": "OCCIDENTAL PETROLEUM CORP /DE/", "symbol": "OXY"}
    (owner,) = buy["reporting_owners"]
    assert owner["cik"] == "1814606" and owner["name"] == "Jackson Richard A." and roles_of(owner) == "director,officer" and owner["officer_title"] == "President and CEO"
    (p,) = buy["non_derivative_transactions"]  # the indirect 401(k) holding row is a position, not a transaction
    assert (p["code"], p["acquired_disposed"], p["shares"], p["price"], p["post_shares"], p["ownership_nature"], p["footnote_ids"]) == ("P", "A", "4770", "52.38", "444098", "D", [])
    assert buy["derivative_transactions"] == [] and buy["footnotes"] == {"F1": "Based on a plan statement dated June 23, 2026."}
    assert CODE_LABELS["P"] == "open-market purchase" and CODE_LABELS["M"].startswith("option exercise")

    # A joint filing: two reporting owners (the holding company and its controlling stockholder) for one set of rows.
    joint = parse_form4(_xml(DVA_JOINT))
    berkshire, buffett = joint["reporting_owners"]
    assert (berkshire["cik"], berkshire["name"], berkshire["ten_percent_owner"], berkshire["officer_title"]) == ("1067983", "BERKSHIRE HATHAWAY INC", True, None)
    assert (buffett["cik"], buffett["name"], buffett["director"], buffett["officer"], buffett["ten_percent_owner"]) == ("315090", "BUFFETT WARREN E", False, False, True)
    (sale,) = joint["non_derivative_transactions"]
    assert (sale["code"], sale["shares"], sale["price"], sale["post_shares"], sale["ownership_nature"], sale["footnote_ids"]) == ("S", "182980", "199.548", "28697229", "I", ["F1", "F2"])
    # Attribution: no owner is a director or officer, so the first owner leads; the roles are the union; a person flagged director leads.
    assert primary_owner(joint["reporting_owners"]) is berkshire and roles_of(*joint["reporting_owners"]) == "ten_percent_owner" and officer_title(berkshire, joint["reporting_owners"]) is None
    trust_first = [{**berkshire, "name": "Some Family Trust", "ten_percent_owner": False, "other": True}, {**buffett, "director": True, "officer": True, "officer_title": "Chief Executive Officer"}]
    assert primary_owner(trust_first)["name"] == "BUFFETT WARREN E" and roles_of(*trust_first) == "director,officer,ten_percent_owner,other"
    assert officer_title(trust_first[0], trust_first) == "Chief Executive Officer" and primary_owner([{"cik": None, "name": "x"}]) is None


def test_parse_form4_tolerates_missing_blocks_and_rejects_other_documents():
    doc = parse_form4("<ownershipDocument><documentType>4/A</documentType><dateOfOriginalSubmission>2026-09-10</dateOfOriginalSubmission></ownershipDocument>")
    assert doc["document_type"] == "4/A" and doc["date_of_original_submission"] == "2026-09-10"
    assert doc["issuer"] == {"cik": None, "name": None, "symbol": None} and doc["reporting_owners"] == [] and doc["aff_10b5_one"] is None
    assert doc["non_derivative_transactions"] == [] and doc["derivative_transactions"] == [] and doc["footnotes"] == {}
    with pytest.raises(ValueError):
        parse_form4("<informationTable/>")


# --- submissions listing + client ------------------------------------------------------------------


def test_recent_filings_from_the_submissions_listing(caplog):
    rows = parse_recent_filings(_submissions("320193"))
    assert [r["form"] for r in rows][:4] == ["4", "10-Q", "8-K", "4"] and len(rows) == 16
    eight_k = next(r for r in rows if r["form"] == "8-K")
    assert eight_k == {"cik": "320193", "form": "8-K", "accession": "0000320193-26-000018", "filed": "2026-07-30", "period": "2026-07-30", "primary_document": "aapl-20260730.htm",
                       "items": ["2.02", "9.01"], "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/0000320193-26-000018-index.htm"}
    form4 = rows[0]
    assert form4["accession"] == AAPL_SALE and form4["items"] == [] and form4["period"] == "2026-09-08" and form4["primary_document"] == "xslF345X06/form4.xml"
    assert [r["form"] for r in parse_recent_filings(_submissions("320193"), forms=("10-K",))] == ["10-K"]
    assert len(parse_recent_filings(_submissions("320193"), limit=3)) == 3
    assert parse_recent_filings({"cik": "1", "filings": {"recent": {}}}) == []
    # `since` cuts the listing to the window before any cap applies: the cap never drops a filing inside the window.
    windowed = parse_recent_filings(_submissions("320193"), since="2026-06-17", limit=None)
    assert [r["filed"] for r in windowed] == ["2026-09-10", "2026-07-31", "2026-07-30", "2026-06-17"] and len(parse_recent_filings(_submissions("320193"), since="2026-06-17", limit=2)) == 2
    with caplog.at_level("WARNING", logger="instilens.sec"):
        assert len(parse_recent_filings(_submissions("320193"), since="2026-01-01", limit=None)) == 13 and not caplog.records  # the block reaches past the window: complete
        assert len(parse_recent_filings(_submissions("320193"), since="2015-01-01", limit=None)) == 16  # older than the block's first entry: what the block has, plus a warning
    assert any("CIK 320193 listing ends at 2025-10-30, inside the window from 2015-01-01" in r.getMessage() for r in caplog.records)

    edgar = _Edgar()
    listing = edgar.client.recent_filings("797468", forms=("4", "8-K"))
    assert edgar.requests == ["https://data.sec.gov/submissions/CIK0000797468.json"]
    assert [r["form"] for r in listing].count("4") == 1 and next(r for r in listing if r["form"] == "4")["accession"] == OXY_BUY
    assert next(r for r in listing if r["accession"] == "0000950157-26-000569")["items"] == ["5.02", "5.07", "7.01", "9.01"]


def test_form4_document_reads_the_raw_xml_or_falls_back_to_the_filing_index():
    edgar = _Edgar()
    assert edgar.client.form4_document("320193", AAPL_SALE, "xslF345X06/form4.xml") == _xml(AAPL_SALE)
    assert edgar.requests == [f"https://www.sec.gov/Archives/edgar/data/320193/{AAPL_SALE.replace('-', '')}/form4.xml"]  # the XSL prefix is dropped, one request
    # An older filing whose primary document is named differently: the guess misses, the index names the XML.
    folder = f"/Archives/edgar/data/797468/{OXY_BUY.replace('-', '')}/"
    edgar.documents[folder] = f'<html><a href="{folder}wk-form4_1782342238.xml">wk-form4_1782342238.xml</a></html>'
    edgar.requests.clear()
    assert edgar.client.form4_document("797468", OXY_BUY, "xslF345X06/doc4.xml") == _xml(OXY_BUY)
    assert [u.rsplit("/", 1)[-1] for u in edgar.requests] == ["doc4.xml", "", "wk-form4_1782342238.xml"]
    with pytest.raises(httpx.HTTPStatusError):
        edgar.client.form4_document("797468", "0001628280-26-000000", None)  # no such filing folder
    edgar.documents["/Archives/edgar/data/797468/000162828026000001/"] = "<html>no xml here</html>"
    with pytest.raises(ValueError):
        edgar.client.form4_document("797468", "0001628280-26-000001", None)  # an index without an XML document


# --- CIK mapping ----------------------------------------------------------------------------------


def test_sec_ciks_mapping(session):
    tmap = tickers.parse_tickers(_ticker_map())
    assert tmap["AAPL"] == "320193" and tmap["OXY"] == "797468" and tmap["LEN-B"] == "920760" and len(tmap) == 179
    assert tickers.normalize_ticker("brk/b") == "BRK-B" == tickers.normalize_ticker("BRK.B")
    aapl, oxy, lenb, nope = _us(session, "AAPL", "OXY", "LEN/B", "NOPE", watch=False)
    placeholder = EntityResolver(session).instrument(Market.US, "CUSIP:037833100")
    asels = EntityResolver(session).instrument(Market.TR, "ASELS")
    assert tickers.sync_ciks(session, tmap) == 3
    assert (aapl.sec_cik, oxy.sec_cik, lenb.sec_cik, nope.sec_cik, placeholder.sec_cik, asels.sec_cik) == ("320193", "797468", "920760", None, None, None)
    aapl.sec_cik = "999"  # a hand-corrected value survives the daily sync; --all re-maps it
    assert tickers.sync_ciks(session, tmap) == 0 and aapl.sec_cik == "999"
    assert tickers.sync_ciks(session, tmap, only_missing=False) == 1 and aapl.sec_cik == "320193"
    edgar = _Edgar()
    oxy.sec_cik = None
    assert tickers.refresh_ciks(session, edgar.client) == 1 and oxy.sec_cik == "797468"
    assert edgar.requests == ["https://www.sec.gov/files/company_tickers.json"]


# --- refresh --------------------------------------------------------------------------------------


def test_refresh_stores_filings_and_transactions_idempotently_and_isolates_issuers(session, monkeypatch, caplog):
    aapl, oxy, googl, nope = _us(session, "AAPL", "OXY", "GOOGL", "NOPE")  # GOOGL: CIK known, no listing in the mock; NOPE: no CIK at all
    edgar = _Edgar()
    naps: list[float] = []
    monkeypatch.setattr(insiders.time, "sleep", naps.append)
    with caplog.at_level("INFO", logger="instilens.insiders"):
        out = insiders.refresh(session, client=edgar.client, as_of=AS_OF, days_back=120, pause_s=0.25)
    assert out == {"issuers": 2, "filings": 8, "form4": 3, "transactions": 5, "skipped": 2}
    # one ticker-map call, one listing per issuer with a CIK (GOOGL's answered 404), one document per new Form 4 — a nap before each but the first
    assert edgar.requests[0].endswith("company_tickers.json") and len(edgar.requests) == 1 + 3 + 3 and naps == [0.25] * 6
    assert (aapl.sec_cik, oxy.sec_cik, googl.sec_cik, nope.sec_cik) == ("320193", "797468", "1652044", None)
    assert aapl.sec_form4_fetched_at is not None and oxy.sec_form4_fetched_at is not None and googl.sec_form4_fetched_at is None
    assert any("GOOGL skipped: HTTP 404" in r.getMessage() for r in caplog.records) and any("no CIK in the SEC ticker map, skipped: NOPE" in r.getMessage() for r in caplog.records)

    # Listings: every form of the window, 8-K items as a list, the report period, the index URL; nothing older than the cut.
    aapl_filings = session.scalars(select(SecFiling).where(SecFiling.instrument_id == aapl.id).order_by(SecFiling.filed_at.desc())).all()
    assert [(f.form, f.filed_at.isoformat()) for f in aapl_filings] == [("4", "2026-09-10"), ("10-Q", "2026-07-31"), ("8-K", "2026-07-30"), ("4", "2026-06-17")]
    ten_q = aapl_filings[1]
    assert ten_q.period == date(2026, 6, 27) and ten_q.items is None and ten_q.accession == "0000320193-26-000020" and ten_q.primary_document == "aapl-20260627.htm"
    assert ten_q.url == "https://www.sec.gov/Archives/edgar/data/320193/000032019326000020/0000320193-26-000020-index.htm"
    assert aapl_filings[2].items == ["2.02", "9.01"]
    assert [f.form for f in session.scalars(select(SecFiling).where(SecFiling.instrument_id == oxy.id))].count("8-K") == 2

    # Disclosures: one per Form 4, parsed on arrival, the payload is the whole document, the URI the filing index.
    discs = {d.source_id: d for d in session.scalars(select(Disclosure).where(Disclosure.kind == "SEC_FORM4"))}
    assert set(discs) == {AAPL_SALE, AAPL_RSU, OXY_BUY} and all(d.source == "SEC" and d.market_code == "US" and d.parse_status == "PARSED" for d in discs.values())
    assert discs[AAPL_SALE].published_at == datetime(2026, 9, 10) and discs[AAPL_SALE].payload["issuer"]["symbol"] == "AAPL"
    assert discs[OXY_BUY].raw_uri == f"https://www.sec.gov/Archives/edgar/data/797468/{OXY_BUY.replace('-', '')}/{OXY_BUY}-index.htm"

    rows = session.scalars(select(InsiderTransaction).where(InsiderTransaction.instrument_id == aapl.id).order_by(InsiderTransaction.transaction_date.desc(), InsiderTransaction.id)).all()
    assert [(r.code, r.derivative, r.acquired, r.shares, r.price) for r in rows] == [  # the June filing was stored first (oldest first); rows here newest first
        ("S", False, False, Decimal("1438"), Decimal("317.23")), ("M", False, True, Decimal("30104"), None), ("F", False, False, Decimal("16238"), Decimal("296.42")),
        ("M", True, False, Decimal("30104"), None)]
    assert {(r.insider_cik, r.insider_name, r.roles, r.title) for r in rows} == {("1780525", "Newstead Jennifer", "officer", "SVP, GC and Government Affairs"),
                                                                                 ("1780525", "Newstead Jennifer", "officer", "SVP, GC and Secretary")}
    assert rows[0].transaction_date == date(2026, 9, 8) and rows[0].filed_at == datetime(2026, 9, 10) and rows[0].post_shares == Decimal("34352") and rows[0].ownership == "D"
    assert rows[0].disclosure_id == discs[AAPL_SALE].id and len({r.row_hash for r in rows}) == 4
    (buy,) = session.scalars(select(InsiderTransaction).where(InsiderTransaction.instrument_id == oxy.id)).all()
    assert (buy.code, buy.acquired, buy.shares, buy.price, buy.roles, buy.title, buy.post_shares) == ("P", True, Decimal("4770"), Decimal("52.38"), "director,officer", "President and CEO", Decimal("444098"))
    assert buy.confidence == "EXACT" and all(r.confidence == "EXACT" for r in rows)  # law 2: every fact row carries its confidence

    # A re-run writes nothing new and fetches no document again.
    edgar.requests.clear()
    again = insiders.refresh(session, client=edgar.client, as_of=AS_OF, days_back=120, pause_s=0)
    assert again == {"issuers": 2, "filings": 0, "form4": 0, "transactions": 0, "skipped": 2}
    assert not any(u.endswith(".xml") for u in edgar.requests)
    assert [u for u in edgar.requests if u.endswith("company_tickers.json")] == ["https://www.sec.gov/files/company_tickers.json"]  # NOPE still lacks a CIK: one map request per run
    assert session.scalar(select(InsiderTransaction.id).where(InsiderTransaction.instrument_id == aapl.id)) is not None
    assert len(session.scalars(select(InsiderTransaction)).all()) == 5 and len(session.scalars(select(SecFiling)).all()) == 8

    # Named symbols are uncapped and the unknown one is reported; a shorter window stores fewer listing rows.
    monkeypatch.setattr(settings, "sec_form4_max_issuers", 1)
    caplog.clear()
    with caplog.at_level("WARNING", logger="instilens.insiders"):
        named = insiders.refresh(session, symbols=["aapl", "oxy", "ZZZ"], client=edgar.client, as_of=AS_OF, days_back=10, pause_s=0)
    assert named["issuers"] == 2 and any("unknown US symbols skipped: ZZZ" in r.getMessage() for r in caplog.records)
    # The cap applies to the universe: one issuer per run, the stalest first (GOOGL was never fetched), then the next.
    edgar.listings["1652044"] = {"cik": "1652044", "filings": {"recent": {"form": [], "accessionNumber": [], "filingDate": [], "reportDate": [], "primaryDocument": [], "items": []}}}
    capped = insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    assert capped["issuers"] == 1 and googl.sec_form4_fetched_at is not None

    # EDGAR refusing (429) ends the run without touching what was written; a 404 on one issuer only skips it.
    edgar.status["submissions/CIK0000320193"] = 429
    for inst in (aapl, oxy, googl):
        inst.sec_form4_fetched_at = None
    session.flush()
    caplog.clear()
    with caplog.at_level("WARNING", logger="instilens.insiders"):
        stopped = insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    assert stopped["issuers"] == 0 and any("EDGAR answered 429 for AAPL, stopping after 0 issuers" in r.getMessage() for r in caplog.records)
    assert len(session.scalars(select(InsiderTransaction)).all()) == 5


def test_a_document_edgar_cannot_serve_skips_only_that_filing(session, caplog):
    (aapl,) = _us(session, "AAPL")
    edgar = _Edgar()
    edgar.status["000114036126025622"] = 404  # the June document is gone (or renamed): the September one still lands
    edgar.documents[f"/Archives/edgar/data/320193/{AAPL_RSU.replace('-', '')}/"] = "<html></html>"
    with caplog.at_level("WARNING", logger="instilens.insiders"):
        out = insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    assert out == {"issuers": 1, "filings": 4, "form4": 1, "transactions": 1, "skipped": 0} and aapl.sec_form4_fetched_at is not None
    assert any(f"AAPL {AAPL_RSU} skipped: HTTPStatusError" in r.getMessage() for r in caplog.records)
    assert [d.source_id for d in session.scalars(select(Disclosure))] == [AAPL_SALE]
    edgar.status.clear()  # back online: the next run fills the gap and nothing else moves
    assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0) == {"issuers": 1, "filings": 0, "form4": 1, "transactions": 3, "skipped": 0}
    # A document that is not an ownership document (an index page served in its place) is skipped the same way.
    edgar.documents[f"/Archives/edgar/data/320193/{AAPL_SALE.replace('-', '')}/form4.xml"] = "<html>not xml</html>"
    session.delete(session.scalar(select(Disclosure).where(Disclosure.source_id == AAPL_SALE)))
    for row in session.scalars(select(InsiderTransaction).where(InsiderTransaction.code == "S")):
        session.delete(row)
    session.flush()
    caplog.clear()
    with caplog.at_level("WARNING", logger="instilens.insiders"):
        assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)["form4"] == 0
    assert any(f"AAPL {AAPL_SALE} skipped: ValueError: not an ownership document" in r.getMessage() for r in caplog.records)


def _amend(edgar: _Edgar, accession: str, filed: str, shares: str, original_day: str | None = "2026-09-10") -> None:
    """A 4/A of the real Apple sale in the mock: a listing entry and the document with `shares` in place of 1,438.
    No small real 4/A exists for these issuers (the closest are from 2022–2023), so the amendment replays the real
    sale under a 4/A listing entry — the way test_sec builds a 13F-HR/A from a real 13F-HR."""
    listing = edgar.listings["320193"]
    recent = listing["filings"]["recent"]
    for col, value in {"form": "4/A", "accessionNumber": accession, "filingDate": filed, "reportDate": "2026-09-08", "primaryDocument": "xslF345X06/form4.xml", "items": ""}.items():
        recent[col].insert(0, value)
    for col in recent:
        if len(recent[col]) < len(recent["form"]):
            recent[col].insert(0, recent[col][0] if recent[col] else "")
    original = f"\n    <dateOfOriginalSubmission>{original_day}</dateOfOriginalSubmission>" if original_day else ""
    edgar.documents[f"/Archives/edgar/data/320193/{accession.replace('-', '')}/form4.xml"] = (
        _xml(AAPL_SALE).replace("<documentType>4</documentType>", f"<documentType>4/A</documentType>{original}").replace("<value>1438</value>", f"<value>{shares}</value>")
    )


def _live_sales(session) -> list[tuple[str, Decimal]]:
    """(accession, shares) of the live S rows, oldest filing first."""
    rows = session.execute(select(Disclosure.source_id, InsiderTransaction.shares).join(InsiderTransaction, InsiderTransaction.disclosure_id == Disclosure.id)
                           .where(InsiderTransaction.code == "S", InsiderTransaction.is_superseded.is_(False)).order_by(Disclosure.published_at, Disclosure.id))
    return [(acc, shares) for acc, shares in rows]


def test_a_failing_issuer_rolls_back_alone_never_the_cik_sync_or_the_callers_work(session):
    """The refresh runs mid-pipeline in the caller's session: an issuer that fails (AAPL's listing answers 404 here)
    undoes its own savepoint only. The CIK sync stays (OXY is read with the CIK it just got), and the caller's
    uncommitted rows survive."""
    aapl, oxy = _us(session, "AAPL", "OXY")
    pending = EntityResolver(session).instrument(Market.US, "PENDING")  # flushed, not committed — the caller's work in flight
    edgar = _Edgar()
    edgar.status["submissions/CIK0000320193"] = 404
    out = insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    assert out == {"issuers": 1, "filings": 4, "form4": 1, "transactions": 1, "skipped": 1}
    assert (aapl.sec_cik, oxy.sec_cik) == ("320193", "797468") and aapl.sec_form4_fetched_at is None and oxy.sec_form4_fetched_at is not None
    assert session.scalar(select(Instrument.id).where(Instrument.symbol == "PENDING")) == pending.id
    assert [d.source_id for d in session.scalars(select(Disclosure))] == [OXY_BUY]
    # An issuer that fails halfway (its second document raises on parse... here EDGAR refuses the listing after the map)
    # leaves nothing of its own behind; what was committed before stays.
    edgar.status.clear()
    edgar.status["submissions/CIK0000320193"] = 500
    for inst in (aapl, oxy):
        inst.sec_form4_fetched_at = None
    session.flush()
    assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)["issuers"] == 0
    assert oxy.sec_form4_fetched_at is None and session.scalar(select(Instrument.id).where(Instrument.symbol == "PENDING")) == pending.id
    assert len(session.scalars(select(InsiderTransaction)).all()) == 1


def test_share_classes_of_one_issuer_share_its_form4_rows(session):
    """GOOG / GOOGL, LEN / LEN-B: one CIK, one listing, one set of Form 4s. A second Apple class stands in (the
    ticker map excerpt does not know it, so its CIK is set by hand, the way a correction would be)."""
    aapl, twin = _us(session, "AAPL", "AAPL-B")
    twin.sec_cik = "320193"
    session.flush()
    edgar = _Edgar()
    out = insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    assert out == {"issuers": 1, "filings": 8, "form4": 2, "transactions": 4, "skipped": 0}  # the listing rows per class, the documents once
    assert [u for u in edgar.requests if "submissions" in u] == ["https://data.sec.gov/submissions/CIK0000320193.json"]
    assert aapl.sec_form4_fetched_at is not None and twin.sec_form4_fetched_at is not None
    assert {r.instrument_id for r in session.scalars(select(InsiderTransaction))} == {aapl.id}  # stored under the class read first
    assert insiders.summary(session, twin.id, 120, AS_OF) == insiders.summary(session, aapl.id, 120, AS_OF)
    assert [t["accession"] for t in insiders.transactions(session, twin.id, 120, as_of=AS_OF)] == [AAPL_SALE, AAPL_RSU, AAPL_RSU, AAPL_RSU]
    body = insiders.stock_insiders(session, "US", "AAPL-B", 120, AS_OF)
    assert body["fetched_at"] is not None and body["summary"]["open_market_sells"] == 1 and [f["form"] for f in insiders.filings(session, twin.id)] == ["4", "10-Q", "8-K", "4"]
    # The cluster fires for every class of the issuer.
    disc = session.scalar(select(Disclosure).where(Disclosure.source_id == AAPL_SALE))
    _insider_rows(session, aapl, disc, ("3001", "A", date(2026, 9, 1), "P"), ("3002", "B", date(2026, 9, 2), "P"), ("3003", "C", date(2026, 9, 3), "P"))
    assert [(inst.symbol, sig.evidence["insiders"]) for inst, sig in insiders.detected_signals(session, AS_OF)] == [("AAPL", 3), ("AAPL-B", 3)]
    # A re-run has nothing to fetch for either class.
    edgar.requests.clear()
    assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)["issuers"] == 1 and len(edgar.requests) == 1


def test_joint_filing_rows_go_to_one_owner_and_to_the_issuer_the_document_names(session):
    """Berkshire Hathaway + Warren Buffett file one Form 4 on DaVita: one set of rows, attributed to the first owner
    (neither is a director or officer) with the union of roles; every owner stays in the payload and the detector
    reads them. An issuer's listing also carries the Form 4s it files as an owner of another company: the rows go
    to that company's instrument, or nowhere."""
    oxy, dva = _us(session, "OXY", "DVA")
    filing = {"accession": DVA_JOINT, "filed": "2026-08-04", "url": f"https://www.sec.gov/Archives/edgar/data/927066/{DVA_JOINT.replace('-', '')}/{DVA_JOINT}-index.htm"}
    doc = parse_form4(_xml(DVA_JOINT))
    # Through Occidental's instrument with no DaVita instrument mapped: the document is kept, no rows.
    oxy.sec_cik = "797468"
    assert insiders._store_form4(session, oxy, filing, doc) == 0
    kept = session.scalar(select(Disclosure).where(Disclosure.source_id == DVA_JOINT))
    assert kept is not None and session.scalar(select(InsiderTransaction.id).where(InsiderTransaction.disclosure_id == kept.id)) is None
    session.delete(kept)
    session.flush()
    # With DaVita mapped, the rows are DaVita's even when the document came through another issuer's listing.
    dva.sec_cik = "927066"
    assert insiders._store_form4(session, oxy, filing, doc) == 1
    (row,) = session.scalars(select(InsiderTransaction)).all()
    assert (row.instrument_id, row.insider_cik, row.insider_name, row.roles, row.title, row.ownership, row.code, row.shares, row.price) == (dva.id, "1067983", "BERKSHIRE HATHAWAY INC", "ten_percent_owner", None, "I", "S", Decimal("182980"), Decimal("199.548"))
    (trade,) = insiders.trades(session, dva.id, date(2026, 7, 1), date(2026, 8, 31))
    assert trade.owner_ciks == ("1067983", "315090") and trade.confidence == "EXACT"
    assert insiders.summary(session, dva.id, 90, date(2026, 8, 31))["sellers"] == 1 and insiders.summary(session, oxy.id, 90, date(2026, 8, 31))["sellers"] == 0


def test_amendment_supersedes_the_original_form4(session):
    (aapl,) = _us(session, "AAPL")
    edgar = _Edgar()
    insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    old = session.scalar(select(Disclosure).where(Disclosure.source_id == AAPL_SALE))
    assert not old.is_superseded and insiders.summary(session, aapl.id, 30, AS_OF)["open_market_sells"] == 1

    amended = "0001140361-26-036300"
    _amend(edgar, amended, "2026-09-14", "1400")
    out = insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    assert out["form4"] == 1 and out["transactions"] == 1
    new = session.scalar(select(Disclosure).where(Disclosure.source_id == amended))
    assert new.supersedes_id == old.id and old.is_superseded and new.payload["document_type"] == "4/A"
    rows = {r.disclosure_id: r for r in session.scalars(select(InsiderTransaction).where(InsiderTransaction.code == "S"))}
    assert rows[old.id].is_superseded and rows[old.id].shares == Decimal("1438") and not rows[new.id].is_superseded and rows[new.id].shares == Decimal("1400")
    s = insiders.summary(session, aapl.id, 30, AS_OF)
    assert (s["sellers"], s["open_market_sells"], s["sell_value"]) == (1, 1, float(Decimal(1400) * Decimal("317.23")))  # the amended row alone
    (tx,) = insiders.transactions(session, aapl.id, 30, as_of=AS_OF)
    assert tx["accession"] == amended and tx["shares"] == 1400.0
    # A 4/A whose original was never fetched supersedes nothing; a re-run changes nothing.
    assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)["form4"] == 0
    assert session.scalar(select(Disclosure).where(Disclosure.source_id == AAPL_RSU)).is_superseded is False

    # A second amendment of the same filing names the same original day, on which nothing is live any more: it replaces
    # the first amendment (the chain original → 4/A #1 → 4/A #2), never leaves two amendments live side by side.
    second = "0001140361-26-036400"
    _amend(edgar, second, "2026-09-15", "1300")
    assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)["form4"] == 1
    newest = session.scalar(select(Disclosure).where(Disclosure.source_id == second))
    assert newest.supersedes_id == new.id and new.is_superseded and old.is_superseded and _live_sales(session) == [(second, Decimal("1300"))]
    s = insiders.summary(session, aapl.id, 30, AS_OF)
    assert (s["open_market_sells"], s["sell_value"]) == (1, float(Decimal(1300) * Decimal("317.23")))
    # A filer who typed the transaction date into the original-date cell (no filing sits on that day) still reaches
    # the same period's latest document.
    third = "0001140361-26-036500"
    _amend(edgar, third, "2026-09-16", "1200", original_day="2026-09-08")
    assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)["form4"] == 1
    assert session.scalar(select(Disclosure).where(Disclosure.source_id == third)).supersedes_id == newest.id and _live_sales(session) == [(third, Decimal("1200"))]


def test_same_day_amendment_is_stored_after_its_original_whatever_the_accession_order(session):
    """Accession prefixes are the filer agent's CIK: a 4/A filed the same day through another agent can sort before
    the original. Within a day the plain forms come first, so the amendment finds the original it replaces."""
    (aapl,) = _us(session, "AAPL")
    edgar = _Edgar()
    _amend(edgar, "0000320193-26-000099", "2026-09-10", "1400")  # sorts before 0001140361-26-036226, filed the same day
    out = insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    assert out["form4"] == 3 and _live_sales(session) == [("0000320193-26-000099", Decimal("1400"))]
    assert session.scalar(select(Disclosure).where(Disclosure.source_id == "0000320193-26-000099")).supersedes_id == session.scalar(select(Disclosure.id).where(Disclosure.source_id == AAPL_SALE))
    assert insiders.summary(session, aapl.id, 30, AS_OF)["open_market_sells"] == 1


def test_an_original_that_arrives_after_its_amendment_is_superseded_on_arrival(session):
    """The original's document could not be served in the run that stored the 4/A (retried next run): when it lands,
    the amendment that names its filing date takes it over at once — the two never count together."""
    (aapl,) = _us(session, "AAPL")
    edgar = _Edgar()
    _amend(edgar, "0001140361-26-036300", "2026-09-14", "1400")
    edgar.status["000114036126036226"] = 404
    edgar.documents[f"/Archives/edgar/data/320193/{AAPL_SALE.replace('-', '')}/"] = "<html></html>"
    assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)["form4"] == 2
    amendment = session.scalar(select(Disclosure).where(Disclosure.source_id == "0001140361-26-036300"))
    assert amendment.supersedes_id is None and _live_sales(session) == [("0001140361-26-036300", Decimal("1400"))]
    edgar.status.clear()
    assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)["form4"] == 1
    original = session.scalar(select(Disclosure).where(Disclosure.source_id == AAPL_SALE))
    assert original.is_superseded and amendment.supersedes_id == original.id and _live_sales(session) == [("0001140361-26-036300", Decimal("1400"))]
    assert insiders.summary(session, aapl.id, 30, AS_OF)["open_market_sells"] == 1


# --- cluster detection (pure) ------------------------------------------------------------------------


def _trade(cik: str, day: date, code: str = "P", shares="100", price="10", *, derivative: bool = False, accession: str | None = None) -> engine.InsiderTrade:
    return engine.InsiderTrade(insider_cik=cik, insider_name=f"Insider {cik}", transaction_date=day, code=code, derivative=derivative,
                               shares=Decimal(shares), price=Decimal(price) if price is not None else None, accession=accession or f"acc-{cik}-{day}")


def test_cluster_needs_three_distinct_open_market_buyers_in_thirty_days():
    as_of = date(2026, 7, 1)
    three = [_trade("1", date(2026, 6, 5)), _trade("2", date(2026, 6, 20), shares="1000", price="50"), _trade("3", date(2026, 6, 30), price=None), _trade("1", date(2026, 6, 25))]
    found = engine.cluster(three, as_of)
    assert found == {"since": "2026-06-05", "insiders": 3, "value": "52000.00", "unpriced": 1, "purchases": 4, "accessions": sorted(t.accession for t in three),
                     "names": ["Insider 1", "Insider 2", "Insider 3"]}
    assert engine.cluster(three[:2] + [three[3]], as_of) is None  # two insiders, three purchases
    assert engine.cluster(three[:2] + [_trade("3", date(2026, 6, 1))], as_of) is None  # the third buyer is exactly 30 days back: outside (start, as_of]
    assert engine.cluster(three[:2] + [_trade("3", date(2026, 6, 2))], as_of)["insiders"] == 3
    # Grants, exercises, tax withholding, sales and every derivative-table row never count.
    never = [_trade("3", date(2026, 6, 30), code=c) for c in ("A", "M", "F", "S", "G")] + [_trade("3", date(2026, 6, 30), derivative=True)]
    assert engine.cluster(three[:2] + never, as_of) is None
    assert engine.cluster(three, as_of, min_insiders=4) is None and engine.cluster(three, date(2026, 8, 15), window_days=90)["insiders"] == 3
    # Strength: 100 × min(1, insiders/5) × (0.5 + 0.5 × min(1, value/1e6)).
    assert engine.cluster_strength(3, Decimal(0)) == 30 and engine.cluster_strength(3, Decimal(1_000_000)) == 60 and engine.cluster_strength(5, Decimal(5_000_000)) == 100
    assert engine.cluster_strength(4, Decimal(500_000)) == 60 and engine.cluster_strength(3, Decimal("52000")) == 32  # 100 × 0.6 × (0.5 + 0.5 × 0.052) = 31.56
    sig = engine.detect_insider_buy_cluster(three, as_of)
    assert sig.signal_type is SignalType.INSIDER_BUY_CLUSTER and sig.strength == 32 and sig.window_start == date(2026, 6, 5) and sig.window_end == as_of
    assert sig.confidence == "EXACT" and sig.evidence["window_days"] == 30 and sig.evidence["insiders"] == 3
    assert engine.detect_insider_buy_cluster(three[:2], as_of) is None
    # Joint filings: a director's own filing, one led by their trust and one by their spouse's trust naming the director
    # are one insider (owner sets overlap), so three filings by two people are no cluster; a third person makes one.
    joint = [
        engine.InsiderTrade("10", "Director Ten", date(2026, 6, 5), "P", False, Decimal(100), Decimal(10), "acc-a", owner_ciks=("10",)),
        engine.InsiderTrade("11", "Ten Family Trust", date(2026, 6, 12), "P", False, Decimal(100), Decimal(10), "acc-b", owner_ciks=("11", "10")),
        engine.InsiderTrade("12", "Ten Spouse Trust", date(2026, 6, 19), "P", False, Decimal(100), Decimal(10), "acc-c", owner_ciks=("12", "10")),
        engine.InsiderTrade("2", "Insider 2", date(2026, 6, 20), "P", False, Decimal(100), Decimal(10), "acc-d"),
    ]
    assert engine.cluster(joint, as_of) is None and [[t.accession for t in g] for g in engine.insider_groups(joint)] == [["acc-a", "acc-b", "acc-c"], ["acc-d"]]
    found = engine.cluster(joint + [_trade("3", date(2026, 6, 30))], as_of)
    assert (found["insiders"], found["purchases"], found["names"]) == (3, 5, ["Director Ten", "Insider 2", "Insider 3"])
    # The signal's confidence is the weakest row's.
    weaker = three[:2] + [engine.InsiderTrade("3", "Insider 3", date(2026, 6, 30), "P", False, Decimal(100), Decimal(10), "acc-e", confidence="INFERRED")]
    assert engine.detect_insider_buy_cluster(weaker, as_of).confidence == "INFERRED"


# --- signals + alerts (DB) ------------------------------------------------------------------------


def _insider_rows(session, inst: Instrument, disc: Disclosure, *rows: tuple[str, str, date, str]) -> None:
    """Extra transaction rows on an existing Form 4 disclosure: (insider cik, name, date, code)."""
    for i, (cik, name, day, code) in enumerate(rows):
        session.add(InsiderTransaction(
            disclosure_id=disc.id, instrument_id=inst.id, insider_cik=cik, insider_name=name, roles="director", title=None, transaction_date=day,
            filed_at=datetime.combine(day, datetime.min.time()), code=code, acquired=code in ("P", "A", "M"), shares=Decimal(100), price=Decimal("50"),
            post_shares=None, ownership="D", derivative=False, row_hash=f"test-{cik}-{i}-{day}-{code}",
        ))
    session.flush()


def test_insider_signals_are_episodic_and_the_alert_fires_once_per_cluster(session):
    (oxy,) = _us(session, "OXY")
    edgar = _Edgar()
    insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)  # Jackson's real purchase of 2026-06-23
    disc = session.scalar(select(Disclosure).where(Disclosure.source_id == OXY_BUY))
    _insider_rows(session, oxy, disc, ("2001", "Second Director", date(2026, 6, 10), "P"), ("2002", "Third Director", date(2026, 6, 28), "A"))
    day = date(2026, 7, 1)
    pipeline.compute_intelligence(session, day)
    assert session.scalar(select(Signal).where(Signal.signal_type == SignalType.INSIDER_BUY_CLUSTER)) is None  # two buyers + one grant: no cluster
    _insider_rows(session, oxy, disc, ("2002", "Third Director", date(2026, 6, 29), "P"))
    pipeline.compute_intelligence(session, day)
    (sig,) = session.scalars(select(Signal).where(Signal.signal_type == SignalType.INSIDER_BUY_CLUSTER)).all()
    assert sig.instrument_id == oxy.id and sig.market_code == "US" and sig.confidence == "EXACT" and (sig.window_start, sig.window_end) == (date(2026, 6, 10), day)
    assert sig.evidence["insiders"] == 3 and sig.evidence["names"] == ["Jackson Richard A.", "Second Director", "Third Director"] and OXY_BUY in sig.evidence["accessions"]
    assert Decimal(sig.evidence["value"]) == Decimal("4770") * Decimal("52.38") + 2 * Decimal(100) * Decimal(50) and sig.strength == engine.cluster_strength(3, Decimal(sig.evidence["value"]))
    # A second compute of the same day (the Form 4 job after the SEC job, a KAP ingest) keeps the row — its id is what
    # alert dedup and outcomes hang on — even when another row has taken the next id in between.
    session.add(Signal(market_code="US", instrument_id=oxy.id, fund_id=None, signal_type="ACCUMULATION", strength=50, window_start=day, window_end=day, evidence={}, confidence="INFERRED"))
    session.flush()
    pipeline.compute_intelligence(session, day)
    (again,) = session.scalars(select(Signal).where(Signal.signal_type == SignalType.INSIDER_BUY_CLUSTER)).all()
    assert again.id == sig.id and again.window_start == date(2026, 6, 10)
    assert session.scalar(select(Signal).where(Signal.signal_type == "ACCUMULATION")) is None  # written for the day, not detected again: gone
    # The next day extends the episode instead of adding a row; once the window has moved past the purchases, nothing is open.
    pipeline.compute_intelligence(session, day + timedelta(days=1))
    rows = session.scalars(select(Signal).where(Signal.signal_type == SignalType.INSIDER_BUY_CLUSTER)).all()
    assert len(rows) == 1 and rows[0].id == sig.id and rows[0].window_end == day + timedelta(days=1)
    pipeline.compute_intelligence(session, date(2026, 8, 15))
    assert len(session.scalars(select(Signal).where(Signal.signal_type == SignalType.INSIDER_BUY_CLUSTER)).all()) == 1  # the July episode stays; no new one
    assert analytics.stock_detail(session, "US", "OXY")["signals"][0]["type"] == "INSIDER_BUY_CLUSTER"

    # Alerts: the watched US stock notifies once for the episode — the dedicated rule, not the generic SIGNAL one twice.
    assert alerts.evaluate(session, day + timedelta(days=1)) == 1
    (note,) = session.scalars(select(Notification)).all()
    assert note.dedup_key.startswith("wl:") and ":INSIDER_BUY_CLUSTER:cluster:" in note.dedup_key and note.link == "/stocks/OXY"
    assert note.title == "OXY: 3 şirket içi kişi son 30 günde açık piyasadan hisse aldı" and "Jackson Richard A." in note.body and "$259,853" in note.body and "2026-06-10" in note.body
    assert "buy" not in note.title.lower() and "al!" not in note.title
    assert alerts.evaluate(session, day + timedelta(days=1)) == 0  # dedup: the same episode never notifies twice
    pipeline.compute_intelligence(session, day + timedelta(days=1))  # nor after a recompute of the day
    assert alerts.evaluate(session, day + timedelta(days=1)) == 0 and len(session.scalars(select(Notification)).all()) == 1
    # An explicit SIGNAL rule of another user still sees it (English text), and an explicit INSIDER_BUY_CLUSTER rule too.
    from instilens.domain.models import AlertRule, User

    session.add(User(email="e@example.com", password_hash="x", name="E", lang="en"))
    session.flush()
    user_id = session.scalar(select(User.id).where(User.email == "e@example.com"))
    session.add_all([AlertRule(owner_id=str(user_id), instrument_id=oxy.id, rule_type="SIGNAL", params={"types": ["INSIDER_BUY_CLUSTER"]}),
                     AlertRule(owner_id=str(user_id), instrument_id=oxy.id, rule_type="INSIDER_BUY_CLUSTER", params={})])
    session.flush()
    assert alerts.evaluate(session, day + timedelta(days=1)) == 2
    titles = sorted(n.title for n in session.scalars(select(Notification).where(Notification.alert_rule_id.is_not(None))))
    assert titles == ["OXY: 3 insiders bought on the open market in the last 30 days", f"OXY: Insider Buy Cluster ({sig.strength})"]
    assert "INSIDER_BUY_CLUSTER" in alerts.RULE_TYPES and alerts.WATCHLIST_US_STOCK_RULES[-1] == "INSIDER_BUY_CLUSTER" and "INSIDER_BUY_CLUSTER" not in alerts.WATCHLIST_STOCK_RULES


# --- read models, routes, stock_detail, tools ---------------------------------------------------------


def test_summary_and_transactions_follow_the_contract(session, monkeypatch):
    aapl, oxy = _us(session, "AAPL", "OXY")
    edgar = _Edgar()
    insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    sale_value = float(Decimal(1438) * Decimal("317.23"))  # Decimal arithmetic, then one conversion — not a float product
    s = insiders.summary(session, aapl.id, 120, AS_OF)
    assert s == {"buyers": 0, "sellers": 1, "buy_value": 0.0, "sell_value": sale_value, "net_value": -sale_value, "open_market_buys": 0, "open_market_sells": 1, "cluster": None}
    assert insiders.summary(session, aapl.id, 30, AS_OF)["open_market_sells"] == 1 and insiders.summary(session, aapl.id, 5, AS_OF)["open_market_sells"] == 0
    rows = insiders.transactions(session, aapl.id, 120, as_of=AS_OF)
    assert [r["code"] for r in rows] == ["S", "M", "F", "M"] and [r["derivative"] for r in rows] == [False, False, False, True]  # newest first, derivative rows flagged
    assert rows[0] == {"id": rows[0]["id"], "transaction_date": "2026-09-08", "filed_at": "2026-09-10T00:00:00", "insider": "Newstead Jennifer", "insider_cik": "1780525", "role": "officer",
                       "title": "SVP, GC and Government Affairs", "code": "S", "acquired": False, "shares": 1438.0, "price": 317.23, "value": sale_value,
                       "post_shares": 34352.0, "ownership": "D", "derivative": False, "confidence": "EXACT", "accession": AAPL_SALE,
                       "url": f"https://www.sec.gov/Archives/edgar/data/320193/{AAPL_SALE.replace('-', '')}/{AAPL_SALE}-index.htm"}
    assert rows[1]["price"] is None and rows[1]["value"] is None and rows[1]["code"] == "M" and rows[1]["acquired"] is True
    assert len(insiders.transactions(session, aapl.id, 120, limit=2, as_of=AS_OF)) == 2
    b = insiders.summary(session, oxy.id, 120, AS_OF)
    buy_value = float(Decimal(4770) * Decimal("52.38"))
    assert (b["buyers"], b["buy_value"], b["open_market_buys"], b["net_value"], b["cluster"]) == (1, buy_value, 1, buy_value, None)
    (buy,) = insiders.transactions(session, oxy.id, 120, as_of=AS_OF)
    assert buy["role"] == "director,officer" and buy["title"] == "President and CEO" and buy["code"] == "P"
    # The payload says when the window holds more than its 200 rows, and where the rest is.
    body = insiders.stock_insiders(session, "US", "AAPL", 120, AS_OF)
    assert body["truncated"] is False and body["edgar_url"] == "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=320193&type=4&dateb=&owner=include&count=100"
    monkeypatch.setattr(insiders, "MAX_TRANSACTIONS", 2)
    body = insiders.stock_insiders(session, "US", "AAPL", 120, AS_OF)
    assert body["truncated"] is True and len(body["transactions"]) == 2 and body["summary"]["open_market_sells"] == 1  # the summary counts every row

    fil = insiders.filings(session, aapl.id)
    assert [f["form"] for f in fil] == ["4", "10-Q", "8-K", "4"]
    assert fil[2] == {"form": "8-K", "filed_at": "2026-07-30", "period": "2026-07-30", "items": ["2.02", "9.01"], "accession": "0000320193-26-000018",
                      "url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/0000320193-26-000018-index.htm",
                      "primary_url": "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/aapl-20260730.htm"}
    assert fil[0]["primary_url"] == f"https://www.sec.gov/Archives/edgar/data/320193/{AAPL_SALE.replace('-', '')}/xslF345X06/form4.xml"
    assert [f["form"] for f in insiders.filings(session, aapl.id, form="8-K")] == ["8-K"] and len(insiders.filings(session, aapl.id, limit=1)) == 1
    fresh = analytics.data_freshness(session, "US")
    assert fresh[1] == {"source": "SEC Form 4", "cadence": "Within two business days of the trade", "last": "2026-09-10", "delayed": False}


def _client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    token = auth.issue_token(auth.register(session, "i@example.com", "correct-horse-1", "I"))
    c = TestClient(app)
    c.headers["authorization"] = f"Bearer {token}"
    return c, app


def test_routes_and_stock_detail_block(session, pipeline_run):
    aapl, oxy = _us(session, "AAPL", "OXY")
    c, app = _client(session)
    try:
        assert TestClient(app).get("/api/v1/stocks/AAPL/insiders", params={"market": "US"}).status_code == 401
        assert TestClient(app).get("/api/v1/stocks/AAPL/filings", params={"market": "US"}).status_code == 401
        assert c.get("/api/v1/stocks/NOPE/insiders", params={"market": "US"}).status_code == 404
        assert c.get("/api/v1/stocks/NOPE/filings", params={"market": "US"}).status_code == 404
        assert c.get("/api/v1/stocks/AAPL/insiders").status_code == 404  # AAPL is not a TR symbol
        for days in (29, 731, 0):
            assert c.get("/api/v1/stocks/AAPL/insiders", params={"market": "US", "days": days}).status_code == 422
        assert c.get("/api/v1/stocks/AAPL/filings", params={"market": "US", "form": "S-1"}).status_code == 422
        assert c.get("/api/v1/stocks/AAPL/filings", params={"market": "US", "limit": 0}).status_code == 422

        tr = c.get("/api/v1/stocks/ASELS/insiders").json()
        assert tr["supported"] is False and tr["symbol"] == "ASELS" and tr["market"] == "TR" and tr["days"] == 90 and tr["source"] == "sec-edgar" and tr["fetched_at"] is None
        assert tr["transactions"] == [] and tr["summary"] == {"buyers": 0, "sellers": 0, "buy_value": 0.0, "sell_value": 0.0, "net_value": 0.0, "open_market_buys": 0, "open_market_sells": 0, "cluster": None}
        assert tr["truncated"] is False and tr["edgar_url"] is None and date.fromisoformat(tr["as_of"]) == date.today()
        assert c.get("/api/v1/stocks/ASELS/filings").json() == {"symbol": "ASELS", "market": "TR", "supported": False, "fetched_at": None, "filings": []}
        assert c.get("/api/v1/stocks/ASELS").json()["insiders"] is None

        # A US symbol nothing has been fetched for yet: supported, empty, fetched_at null; stock_detail keeps null (no zeros before a fetch).
        empty = c.get("/api/v1/stocks/AAPL/insiders", params={"market": "US"}).json()
        assert empty["supported"] is True and empty["fetched_at"] is None and empty["transactions"] == [] and empty["summary"]["buyers"] == 0
        assert c.get("/api/v1/stocks/aapl", params={"market": "US"}).json()["insiders"] is None
        unread = c.get("/api/v1/stocks/AAPL/filings", params={"market": "US"}).json()
        assert unread == {"symbol": "AAPL", "market": "US", "supported": True, "fetched_at": None, "filings": []}  # "not read yet", never "no filings"

        insiders.refresh(session, client=_Edgar().client, as_of=AS_OF, pause_s=0)
        body = c.get("/api/v1/stocks/aapl/insiders", params={"market": "US", "days": 730}).json()
        assert set(body) == {"symbol", "name", "market", "supported", "days", "as_of", "source", "fetched_at", "summary", "transactions", "truncated", "edgar_url"}
        assert body["symbol"] == "AAPL" and body["supported"] is True and body["days"] == 730 and body["source"] == "sec-edgar" and datetime.fromisoformat(body["fetched_at"])
        assert set(body["summary"]) == {"buyers", "sellers", "buy_value", "sell_value", "net_value", "open_market_buys", "open_market_sells", "cluster"}
        assert (body["summary"]["sellers"], body["summary"]["open_market_sells"], body["summary"]["cluster"]) == (1, 1, None)
        assert [t["code"] for t in body["transactions"]] == ["S", "M", "F", "M"] and body["truncated"] is False and "CIK=320193" in body["edgar_url"]
        assert set(body["transactions"][0]) == {"id", "transaction_date", "filed_at", "insider", "insider_cik", "role", "title", "code", "acquired", "shares", "price",
                                               "value", "post_shares", "ownership", "derivative", "confidence", "accession", "url"}
        assert body["transactions"][0]["accession"] == AAPL_SALE and body["transactions"][0]["url"].endswith(f"{AAPL_SALE}-index.htm")
        oxy_body = c.get("/api/v1/stocks/OXY/insiders", params={"market": "US", "days": 730}).json()
        assert oxy_body["summary"]["buyers"] == 1 and oxy_body["transactions"][0]["code"] == "P" and oxy_body["transactions"][0]["confidence"] == "EXACT"

        fil = c.get("/api/v1/stocks/AAPL/filings", params={"market": "US"}).json()
        assert fil["symbol"] == "AAPL" and fil["supported"] is True and datetime.fromisoformat(fil["fetched_at"]) and [f["form"] for f in fil["filings"]] == ["4", "10-Q", "8-K", "4"]
        assert set(fil["filings"][0]) == {"form", "filed_at", "period", "items", "accession", "url", "primary_url"}
        assert [f["form"] for f in c.get("/api/v1/stocks/AAPL/filings", params={"market": "US", "form": "8-K", "limit": 5}).json()["filings"]] == ["8-K"]
        assert c.get("/api/v1/stocks/AAPL/filings", params={"market": "US", "form": "8-K"}).json()["filings"][0]["items"] == ["2.02", "9.01"]

        detail = c.get("/api/v1/stocks/AAPL", params={"market": "US"}).json()["insiders"]
        assert set(detail) == {"days", "buyers", "sellers", "net_value", "cluster"} and detail["days"] == 90 and detail["cluster"] is False
        assert detail == insiders.detail(session, aapl) and insiders.detail(session, oxy)["buyers"] in (0, 1)  # the 90-day window is measured from today
        assert c.get("/api/v1/stocks/ASELS").json()["insiders"] is None

        # The dedicated rule needs a US symbol: Form 4 data exists for US issuers only, so a BIST rule would never fire.
        assert c.post("/api/v1/alerts/rules", json={"symbol": "OXY", "market": "US", "rule_type": "INSIDER_BUY_CLUSTER"}).status_code == 201
        refused = c.post("/api/v1/alerts/rules", json={"symbol": "ASELS", "market": "TR", "rule_type": "INSIDER_BUY_CLUSTER"})
        assert refused.status_code == 400 and "US symbol" in refused.json()["detail"]
        assert c.post("/api/v1/alerts/rules", json={"fund_code": "TMV", "market": "TR", "rule_type": "INSIDER_BUY_CLUSTER"}).status_code == 400
    finally:
        app.dependency_overrides.clear()


def test_tools_return_compact_json_and_name_the_codes(session):
    from anthropic import beta_tool

    from instilens.ai.prompts import SYSTEM_PROMPT
    from instilens.ai.tools import build_tools

    _us(session, "AAPL", "OXY")
    insiders.refresh(session, client=_Edgar().client, as_of=AS_OF, pause_s=0)
    tools = {fn.__name__: beta_tool(fn) for fn in build_tools(session, "US")}
    desc = tools["get_insider_trades"].to_dict()["description"]
    for phrase in ("P = open-market", "M = option exercise", "F = shares withheld", "not recommendations", "accession"):
        assert phrase in desc
    assert "8-K" in tools["get_filings"].to_dict()["description"] and tools["get_filings"].to_dict()["input_schema"]["properties"]["form"]["anyOf"][0]["enum"] == ["8-K", "10-K", "10-Q", "4", "4/A"]
    for phrase in ("`fetched_at`", "has not been read yet", "`truncated: true`"):
        assert phrase in desc
    assert "not been read yet" in tools["get_filings"].to_dict()["description"]
    trades = json.loads(tools["get_insider_trades"].call({"symbol": "aapl", "days": 730, "limit": 2}))
    assert trades["symbol"] == "AAPL" and trades["supported"] is True and trades["days"] == 730 and len(trades["transactions"]) == 2 and trades["truncated"] is True
    assert trades["transactions"][0]["code"] == "S" and trades["transactions"][0]["accession"] == AAPL_SALE and trades["summary"]["open_market_sells"] == 1
    assert "code_label_key" not in trades["transactions"][0] and trades["transactions"][0]["confidence"] == "EXACT" and datetime.fromisoformat(trades["fetched_at"])
    whole = json.loads(tools["get_insider_trades"].call({"symbol": "OXY", "days": 5000}))
    assert whole["days"] == 730 and whole["truncated"] is False  # clamped to the route's bounds
    fil = json.loads(tools["get_filings"].call({"symbol": "aapl", "form": "8-K", "limit": 1}))
    assert fil["supported"] is True and [f["form"] for f in fil["filings"]] == ["8-K"] and fil["filings"][0]["items"] == ["2.02", "9.01"]
    assert len(json.loads(tools["get_filings"].call({"symbol": "AAPL"}))["filings"]) == 4
    assert "error" in json.loads(tools["get_insider_trades"].call({"symbol": "NOPE"})) and "error" in json.loads(tools["get_filings"].call({"symbol": "NOPE"}))
    tr_tools = {fn.__name__: beta_tool(fn) for fn in build_tools(session, "TR")}
    assert json.loads(tr_tools["get_insider_trades"].call({"symbol": "AAPL"})) == {"error": "unknown symbol AAPL"}
    assert "get_insider_trades" in SYSTEM_PROMPT and "option exercise" in SYSTEM_PROMPT and "US issuers only" in SYSTEM_PROMPT


# --- migration ------------------------------------------------------------------------------------


def test_migration_creates_insider_tables_on_scratch_sqlite(tmp_path, monkeypatch):
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
    insp = inspect(eng)
    assert {"insider_transactions", "sec_filings"} <= set(insp.get_table_names())
    assert {c["name"] for c in insp.get_columns("insider_transactions")} == {"id", "disclosure_id", "instrument_id", "insider_cik", "insider_name", "roles", "title", "transaction_date",
                                                                            "filed_at", "code", "acquired", "shares", "price", "post_shares", "ownership", "derivative", "is_superseded", "confidence", "row_hash"}
    assert {c["name"] for c in insp.get_columns("sec_filings")} == {"id", "instrument_id", "form", "filed_at", "period", "items", "accession", "primary_document", "url"}
    assert {"sec_cik", "sec_form4_fetched_at"} <= {c["name"] for c in insp.get_columns("instruments")}
    assert {"instrument_id", "accession"} in [set(u["column_names"]) for u in insp.get_unique_constraints("sec_filings")]
    assert {"row_hash"} in [set(u["column_names"]) for u in insp.get_unique_constraints("insider_transactions")]
    assert "ix_instruments_sec_cik" in {i["name"] for i in insp.get_indexes("instruments")}
    with eng.begin() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "e1f2a3b4c5d6"
        conn.execute(text("INSERT INTO sec_filings (instrument_id, form, filed_at, accession, url) VALUES (1, '8-K', '2026-07-30', '0000320193-26-000018', 'u')"))
        with pytest.raises(Exception):  # noqa: B017 — the unique key, whatever the driver calls the violation
            conn.execute(text("INSERT INTO sec_filings (instrument_id, form, filed_at, accession, url) VALUES (1, '8-K', '2026-07-30', '0000320193-26-000018', 'u')"))
    eng.dispose()
    command.downgrade(cfg, "d0e1f2a3b4c5")
    eng = create_engine(url)
    insp = inspect(eng)
    assert {"insider_transactions", "sec_filings"}.isdisjoint(insp.get_table_names())
    assert {"sec_cik", "sec_form4_fetched_at"}.isdisjoint(c["name"] for c in insp.get_columns("instruments"))
    assert "fundamentals" in insp.get_table_names()
    eng.dispose()


# --- universe + scheduler ---------------------------------------------------------------------------


def test_universe_is_flows_and_watchlists_stalest_first_and_capped(session, monkeypatch):
    resolver = EntityResolver(session)
    resolver.ensure_markets()
    aapl, oxy, stale, idle = (resolver.instrument(Market.US, s) for s in ("AAPL", "OXY", "STALE", "IDLE"))
    placeholder = resolver.instrument(Market.US, "CUSIP:037833100")
    asels = resolver.instrument(Market.TR, "ASELS")
    institution = resolver.institution(Market.US, "1067983", "Berkshire")
    fund = resolver.fund("CIK1067983", institution)
    today = date.today()
    snap = PortfolioSnapshot(fund_id=fund.id, as_of=today, source="SEC")
    session.add(snap)
    session.flush()

    def change(inst, days_ago):
        return PositionChange(fund_id=fund.id, instrument_id=inst.id, to_snapshot_id=snap.id, period_end=today - timedelta(days=days_ago), from_qty=0, to_qty=10, delta_qty=10, activity="NEW")

    session.add_all([change(aapl, 10), change(stale, insiders.UNIVERSE_DAYS + 1), change(placeholder, 10), change(asels, 1)])
    watchlist = Watchlist(owner_id="1", name="w")
    session.add(watchlist)
    session.flush()
    session.add_all([WatchlistItem(watchlist_id=watchlist.id, instrument_id=oxy.id), WatchlistItem(watchlist_id=watchlist.id, fund_id=fund.id)])
    session.flush()
    assert [i.symbol for i in insiders.universe(session, mapped=None)] == ["AAPL", "OXY"]  # STALE too old, IDLE seen nowhere, the CUSIP placeholder and the TR stock never
    assert insiders.universe(session) == [] and [i.symbol for i in insiders.universe(session, mapped=False)] == ["AAPL", "OXY"]  # nothing carries a CIK yet
    assert idle.symbol == "IDLE"
    session.add(change(idle, 3))
    now = datetime.now(UTC)
    aapl.sec_form4_fetched_at, oxy.sec_form4_fetched_at = now - timedelta(days=1), now - timedelta(days=8)
    session.flush()
    assert [i.symbol for i in insiders.universe(session, mapped=None)] == ["IDLE", "OXY", "AAPL"]  # never fetched first, then the oldest fetch
    assert [i.symbol for i in insiders.universe(session, limit=2, mapped=None)] == ["IDLE", "OXY"]
    # The refresh cap comes from the runtime setting; CIK-less issuers are asked for one first, in a single request, and the
    # batch is chosen after that: IDLE (unknown to the SEC map) is reported as skipped and never holds a slot of the cap.
    monkeypatch.setattr(settings, "sec_form4_max_issuers", 1)
    edgar = _Edgar()
    out = insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)
    assert out["issuers"] == 1 and out["skipped"] == 1 and oxy.sec_form4_fetched_at > now and aapl.sec_form4_fetched_at < now  # OXY (stalest) fetched; AAPL beyond the cap
    assert [u for u in edgar.requests if "company_tickers" in u] == ["https://www.sec.gov/files/company_tickers.json"]
    assert [i.symbol for i in insiders.universe(session)] == ["AAPL", "OXY"] and [i.symbol for i in insiders.universe(session, mapped=False)] == ["IDLE"]
    monkeypatch.setattr(settings, "sec_form4_max_issuers", 2)
    assert insiders.refresh(session, client=edgar.client, as_of=AS_OF, pause_s=0)["issuers"] == 2 and aapl.sec_form4_fetched_at > now  # the cap is spent on issuers that can be read
    from instilens.services import runtime_settings

    assert runtime_settings.EDITABLE["sec_form4_enabled"] == {"type": "bool", "group": "data"} and runtime_settings.coerce("sec_form4_enabled", "off") is False
    assert runtime_settings.EDITABLE["sec_form4_max_issuers"] == {"type": "int", "group": "data", "min": 10, "max": 2000} and runtime_settings.coerce("sec_form4_max_issuers", "250") == 250
    with pytest.raises(runtime_settings.SettingError):
        runtime_settings.coerce("sec_form4_max_issuers", 5)


def test_scheduler_job_is_gated_and_recomputes_after_new_filings(monkeypatch):
    import contextlib

    from instilens import scheduler

    calls: list[str] = []
    results = iter([{"issuers": 1, "filings": 2, "form4": 0, "transactions": 0, "skipped": 0}, {"issuers": 1, "filings": 3, "form4": 1, "transactions": 2, "skipped": 0}])
    monkeypatch.setattr(insiders, "refresh", lambda s: calls.append("refresh") or next(results))
    monkeypatch.setattr(scheduler, "compute", lambda: calls.append("compute"))
    monkeypatch.setattr(scheduler, "session_scope", lambda: contextlib.nullcontext(object()))
    monkeypatch.setattr(settings, "sec_form4_enabled", False)
    scheduler.form4_daily()
    assert calls == []
    monkeypatch.setattr(settings, "sec_form4_enabled", True)
    scheduler.form4_daily()
    assert calls == ["refresh"]  # nothing new: no recompute
    scheduler.form4_daily()
    assert calls == ["refresh", "refresh", "compute"]  # a new Form 4 feeds the insider signals right away
