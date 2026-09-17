"""Full-text search: the indexer over real fixture documents (KAP, SEC 13F, Form 4, EDGAR listing, headlines, AI
notes), its incremental watermark, the SQLite LIKE engine (ranking, snippets, kinds, escaping), the route and the
AI tool. The Postgres engine (websearch_to_tsquery / ts_rank_cd / ts_headline over the generated tsvector column)
cannot run on the SQLite test database: `text_search` dispatches on the dialect, and everything after the query —
kinds, limit, the hit shape, the snippet marks, the title fallback — is shared code covered here."""

import csv
import json
from datetime import UTC, date, datetime, timedelta

import pytest
from anthropic import beta_tool
from fastapi.testclient import TestClient
from sqlalchemy import select, update

from instilens.ai.openai_tools import function_schema
from instilens.ai.tools import build_tools
from instilens.domain.enums import Market
from instilens.domain.models import (
    AiNote,
    Disclosure,
    Instrument,
    NewsItem,
    SearchableText,
    SecFiling,
)
from instilens.ingestion.sec.fixture_adapter import SecFixtureAdapter
from instilens.ingestion.sec.form4 import parse_form4
from instilens.services import auth, pipeline, runtime_settings, search, search_index
from instilens.services.entities import EntityResolver
from tests.conftest import AS_OF, FIXTURES

AAPL_SALE = "0001140361-26-036226"  # Apple, Newstead: S 1,438 @ 317.23 (filed 2026-09-10), one 10b5-1 footnote
BRIDGEWATER_Q4 = "0001350694-26-000001"  # 13F-HR, period 2025-12-31


def _rows(session, kind: str | None = None) -> list[SearchableText]:
    stmt = select(SearchableText).order_by(SearchableText.id)
    return session.scalars(stmt.where(SearchableText.kind == kind) if kind else stmt).all()


def _by_title(session, fragment: str) -> SearchableText:
    (row,) = [r for r in _rows(session) if fragment in r.title]
    return row


def _news(session, title: str, *, symbols=(), ai=None, when=datetime(2026, 9, 12, 9, 0), market="TR", url=None) -> NewsItem:
    n = NewsItem(market_code=market, source="Bloomberg HT", title=title, url=url or f"https://news.example/{abs(hash(title))}", published_at=when,
                 symbols=list(symbols), tags=["Savunma"] if symbols else [], ai=ai)
    session.add(n)
    session.flush()
    return n


def _us_documents(session) -> tuple[Instrument, Disclosure, SecFiling]:
    """Real SEC documents next to the KAP fixtures: the nine 13F filings (ingested, with the CUSIP map so the largest
    holdings resolve to tickers), Apple's Form 4 sale as a parsed disclosure, one 8-K listing row."""
    resolver = EntityResolver(session)
    resolver.ensure_markets()
    with open(FIXTURES / "cusips_US.csv", newline="", encoding="utf-8") as f:
        resolver.load_cusip_map(list(csv.DictReader(f)))
    assert pipeline.ingest(session, SecFixtureAdapter(FIXTURES / "sec")) == 9
    aapl = resolver.instrument(Market.US, "AAPL")
    aapl.name = "Apple Inc."
    payload = parse_form4((FIXTURES / "sec" / "form4" / f"{AAPL_SALE}.form4.xml").read_text(encoding="utf-8"))
    form4 = Disclosure(market_code="US", source="SEC", source_id=AAPL_SALE, kind="SEC_FORM4", published_at=datetime(2026, 9, 10), raw_hash="x",
                       raw_uri=f"https://www.sec.gov/Archives/edgar/data/320193/{AAPL_SALE.replace('-', '')}/{AAPL_SALE}-index.htm", payload=payload, parse_status="PARSED")
    filing = SecFiling(instrument_id=aapl.id, form="8-K", filed_at=date(2026, 7, 30), period=date(2026, 7, 30), items=["2.02", "9.01"], accession="0000320193-26-000018",
                       primary_document="aapl-20260730.htm", url="https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/0000320193-26-000018-index.htm")
    session.add_all([form4, filing])
    session.flush()
    return aapl, form4, filing


# --- indexer ------------------------------------------------------------------------------------


def test_indexer_extracts_kap_sec_news_and_note_texts(session, pipeline_run):
    aapl, form4, filing = _us_documents(session)
    _news(session, "Aselsan <b>savunma</b> ihracatında rekor kırdı", symbols=["ASELS"], ai={"summary_tr": "Aselsan 2026'da ihracat rekoru kırdı.", "sector": "Savunma", "sentiment": "positive", "relevance": 80})
    session.add(AiNote(kind="STOCK_ASSESSMENT", market_code="TR", subject="ASELS", as_of=AS_OF, lang="tr", content="Fonlar ASELS'te net alıcıydı.<br/>\n\nÜç fon pozisyon artırdı.",
                       data={"headline": "12 fon ASELS pozisyonunu artırdı", "highlights": ["Net akış +843M"], "watch": ["Eylül raporu"], "confidence_note": "Tutarlar gruplu."}, model="test"))
    session.add(AiNote(kind="DAILY_BRIEF", market_code="US", subject="market", as_of=AS_OF, lang="en", content="Berkshire added Alphabet.", data={"watch": []}, model="test"))
    session.commit()

    counts = search_index.reindex(session)
    assert counts == {"disclosure": 25 + 9 + 1, "filing": 1, "news": 1, "note": 2}
    assert {(r.kind, r.ref_id) for r in _rows(session)} == {(r.kind, r.ref_id) for r in _rows(session)} and len(_rows(session)) == sum(counts.values())

    # KAP share transaction: the correction and the notice it supersedes, in Turkish, with the KAP ids and the figures as KAP writes them.
    fix = _by_title(session, "Düzeltme — Pay Alım Satım Bildirimi · ANELE")
    assert fix.market_code == "TR" and fix.source == "KAP" and fix.symbols == ["ANELE"] and fix.link == "/stocks/ANELE" and fix.date == date(2026, 9, 11)
    assert fix.url == "https://www.kap.org.tr/tr/Bildirim/1608450" and "KAP #1608450" in fix.body
    assert "Pay: ANELE" in fix.body and "İlgili fonlar: TMV, TLY" in fix.body and "2026-09-10 Alış 46.526.835 nominal · fiyat 12,5 TL" in fix.body and "%0 → %17,56" in fix.body
    assert "Düzeltilen bildirim: KAP #1608319" in fix.body
    old = session.scalar(select(SearchableText).where(SearchableText.kind == "disclosure", SearchableText.ref_id == session.scalar(select(Disclosure.id).where(Disclosure.source_id == "1608319"))))
    assert old.title.startswith("Düzeltildi — Pay Alım Satım Bildirimi · ANELE") and "geçersiz kılındı" in old.body and "geçersiz" not in fix.body
    assert old.superseded is True and fix.superseded is False and not fix.title.startswith("Düzeltildi")
    # KAP portfolio report: fund, manager, every holding line; the holdings are the symbols, the fund page the link.
    tmv = [r for r in _rows(session, "disclosure") if r.title.startswith("Fon Portföy Dağılım Raporu · TMV")]
    assert len(tmv) == 4 and tmv[0].symbols == ["ASELS", "SASA", "EREGL", "KCHOL"] and tmv[0].link == "/funds/TMV"
    assert "Kurucu / yönetici: Tera Portföy Yönetimi A.Ş." in tmv[0].body and "ASELS: 1.000.000 adet · 150.000.000 TL · %57,1211" in tmv[0].body
    # SEC 13F: filer, period, issuer names with CUSIPs largest first; tickers only where the CUSIP map knows them; body capped.
    bw = _by_title(session, "13F-HR · Bridgewater Associates, LP · period 2025-12-31")
    assert bw.market_code == "US" and bw.source == "SEC" and bw.link == "/funds/CIK1350694" and bw.url.startswith("https://www.sec.gov/Archives/edgar/data/1350694/")
    assert f"Accession {BRIDGEWATER_Q4}" in bw.body and "ABBOTT LABS (CUSIP 002824100): 122,694 sh · $15,372,331 · 0.0561 %" in bw.body
    assert bw.symbols and all(not s.startswith("CUSIP:") for s in bw.symbols) and len(bw.symbols) <= search_index.SYMBOLS_MAX
    assert len(bw.body) <= search_index.BODY_MAX and bw.body.index("Holdings (shares only") < bw.body.index("ABBOTT LABS")
    assert [r.title for r in _rows(session, "disclosure") if "13F-HR/A" in r.title] == []  # no amendment among the fixtures
    # SEC Form 4: issuer, owner with role and title, the transaction with its code spelled out, the footnote text.
    f4 = _by_title(session, "Form 4 · Apple Inc. (AAPL) · Newstead Jennifer")
    assert f4.symbols == ["AAPL"] and f4.link == "/stocks/AAPL" and f4.url == form4.raw_uri and f4.date == date(2026, 9, 10)
    assert "Reporting owner: Newstead Jennifer — officer (SVP, GC and Government Affairs)" in f4.body
    assert "2026-09-08 code S (open-market or private sale): disposed of 1,438 Common Stock @ 317.23, 34,352 held after (direct)" in f4.body
    assert "Rule 10b5-1 trading plan" in f4.body and "F1:" in f4.body
    # EDGAR listing row: form label, 8-K item titles, accession; the stock page as the link.
    (fl,) = _rows(session, "filing")
    assert fl.title == "8-K current report · AAPL · Apple Inc." and fl.ref_id == filing.id and fl.market_code == "US" and fl.date == date(2026, 7, 30)
    assert "Items: 2.02 Results of Operations and Financial Condition; 9.01 Financial Statements and Exhibits" in fl.body
    assert "Accession 0000320193-26-000018 · primary document aapl-20730.htm".replace("20730", "20260730") in fl.body and fl.url == filing.url and fl.link == "/stocks/AAPL"
    # Headline: tags stripped from the title, the AI summary and the rule tags in the body, the first symbol's page as the link.
    (nw,) = _rows(session, "news")
    assert nw.title == "Aselsan savunma ihracatında rekor kırdı" and "<b>" not in nw.title
    assert nw.body == "Aselsan 2026'da ihracat rekoru kırdı.\nSektör: Savunma\nEtiketler: Savunma\nKaynak: Bloomberg HT"
    assert nw.symbols == ["ASELS"] and nw.link == "/stocks/ASELS" and nw.source == "Bloomberg HT" and nw.url.startswith("https://news.example/") and nw.date == date(2026, 9, 12)
    # AI notes: the headline is the title, the narrative plus highlights / watch / confidence note the body, no url and
    # no link (the stock page shows the current note, not this one — the symbol chip is the way there).
    tr, en = _rows(session, "note")
    assert tr.title == "12 fon ASELS pozisyonunu artırdı" and tr.symbols == ["ASELS"] and tr.link is None and tr.source == "InstiLens AI" and tr.url is None
    assert tr.body == "Fonlar ASELS'te net alıcıydı.\n\nÜç fon pozisyon artırdı.\nÖne çıkanlar: Net akış +843M\nİzlenecekler: Eylül raporu\nTutarlar gruplu."
    assert en.title == f"Daily brief · US · {AS_OF.isoformat()}" and en.symbols == [] and en.link is None and en.market_code == "US"
    assert all(r.updated_at is not None and r.superseded is False for r in _rows(session) if r.kind != "disclosure" or "Düzeltildi" not in r.title)


def test_reindex_is_incremental_and_full_rebuilds(session, pipeline_run):
    aapl, _, filing = _us_documents(session)
    untagged = _news(session, "Merkez Bankası faiz kararını açıkladı")  # no AI pass yet
    session.add(AiNote(kind="DAILY_BRIEF", market_code="TR", subject="market", as_of=AS_OF, lang="tr", content="Fonlar net alıcıydı.", data={"watch": []}, model="test"))
    session.commit()
    first = search_index.reindex(session)
    session.commit()
    assert first == {"disclosure": 35, "filing": 1, "news": 1, "note": 1}
    state = runtime_settings.load_json(session, search_index.WATERMARK_KEY)
    assert set(state) == {"TR", "US"} and state["US"]["filing_id"] == filing.id and state["TR"]["news_pending_ai"] == [untagged.id]
    assert datetime.fromisoformat(state["TR"]["since"]) <= datetime.now(UTC).replace(tzinfo=None)

    # Nothing changed and the rows are older than the slack window: the second run writes nothing — the pending
    # headline is re-read until its summary exists, but an unchanged row is not rewritten.
    earlier = datetime.now(UTC).replace(tzinfo=None) - search_index.SLACK - timedelta(hours=1)
    session.execute(update(Disclosure).values(ingested_at=earlier))
    session.execute(update(NewsItem).values(fetched_at=earlier))
    session.execute(update(AiNote).values(created_at=earlier))
    session.commit()
    stamps = {(r.kind, r.ref_id): r.updated_at for r in _rows(session)}
    assert search_index.reindex(session) == {"disclosure": 0, "filing": 0, "news": 0, "note": 0}
    assert {(r.kind, r.ref_id): r.updated_at for r in _rows(session)} == stamps and runtime_settings.load_json(session, search_index.WATERMARK_KEY)["TR"]["news_pending_ai"] == [untagged.id]
    stamp = session.scalar(select(SearchableText.updated_at).where(SearchableText.kind == "note"))

    # A new headline, a new listing row, a tagged summary, a rewritten note: only those are (re)written.
    fresh = _news(session, "Türk Hava Yolları yolcu sayısını açıkladı", symbols=["THYAO"], when=datetime(2026, 9, 13, 8, 0))
    untagged.ai = {"summary_tr": "TCMB politika faizini sabit tuttu.", "sector": "Makro", "sentiment": "neutral", "relevance": 90}
    session.add(SecFiling(instrument_id=aapl.id, form="10-Q", filed_at=date(2026, 7, 31), period=date(2026, 6, 27), accession="0000320193-26-000020", url="https://sec.example/10q"))
    note = session.scalar(select(AiNote))
    note.content, note.created_at = "Fonlar net satıcıydı.", datetime.now(UTC)
    session.commit()
    assert search_index.reindex(session) == {"disclosure": 0, "filing": 1, "news": 2, "note": 1}
    state = runtime_settings.load_json(session, search_index.WATERMARK_KEY)
    assert state["TR"]["news_pending_ai"] == [fresh.id] and state["US"]["filing_id"] > filing.id  # tagged one out, the new untagged one in
    tagged = session.scalar(select(SearchableText).where(SearchableText.kind == "news", SearchableText.ref_id == untagged.id))
    assert "TCMB politika faizini sabit tuttu." in tagged.body and session.scalar(select(SearchableText.body).where(SearchableText.kind == "note")) .startswith("Fonlar net satıcıydı.")
    assert session.scalar(select(SearchableText.updated_at).where(SearchableText.kind == "note")) >= stamp
    assert session.scalar(select(SearchableText.title).where(SearchableText.kind == "news", SearchableText.ref_id == fresh.id)) == fresh.title

    # A correction arriving later re-indexes the notice it supersedes (its own timestamp never changes).
    target = session.scalar(select(Disclosure).where(Disclosure.source_id == "1608402"))
    assert "geçersiz" not in session.scalar(select(SearchableText.body).where(SearchableText.kind == "disclosure", SearchableText.ref_id == target.id))
    later = Disclosure(market_code="TR", source="KAP", source_id="1608999", kind="KAP_SHARE_TRANSACTION", published_at=datetime(2026, 9, 14, 18, 0), raw_hash="y",
                       raw_uri="https://www.kap.org.tr/tr/Bildirim/1608999", payload={**target.payload, "amends_source_id": "1608402"}, parse_status="PARSED", supersedes_id=target.id)
    target.is_superseded = True
    session.add(later)
    session.commit()
    counts = search_index.reindex(session, market="TR")
    assert counts["disclosure"] == 2 and counts["filing"] == 0  # the correction and its target; the pending headline and the just-rewritten note (inside the slack window) are re-read unchanged
    superseded = session.scalar(select(SearchableText).where(SearchableText.kind == "disclosure", SearchableText.ref_id == target.id))
    assert "geçersiz kılındı" in superseded.body and superseded.superseded is True and superseded.title.startswith("Düzeltildi — ")

    # `since` re-reads from a day (filings by their filing date, the rest by their timestamps) and repairs what differs;
    # a market run leaves the other market alone; `full` drops orphans and rebuilds.
    bw = _by_title(session, "13F-HR · Bridgewater Associates, LP · period 2025-12-31")
    tenq, eightk = (session.scalar(select(SearchableText).where(SearchableText.kind == "filing", SearchableText.title.startswith(form))) for form in ("10-Q", "8-K"))
    bw.title = tenq.title = eightk.title = "stale"
    session.commit()
    assert search_index.reindex(session, market="US", since=datetime(2026, 9, 1)) == {"disclosure": 1, "filing": 0, "news": 0, "note": 0}  # ten SEC rows re-read, one differed
    assert bw.title.startswith("13F-HR · Bridgewater") and tenq.title == "stale"
    assert search_index.reindex(session, market="US", since=datetime(2026, 7, 31)) == {"disclosure": 0, "filing": 1, "news": 0, "note": 0}  # the 10-Q of 07-31, not the 8-K of 07-30
    assert tenq.title.startswith("10-Q quarterly report") and eightk.title == "stale"
    assert runtime_settings.load_json(session, search_index.WATERMARK_KEY)["TR"]["news_pending_ai"] == [fresh.id]
    # A headline deleted at the source leaves the index on the next news pass, and the pending list with it.
    session.delete(fresh)
    session.commit()
    assert len(_rows(session, "news")) == 2
    assert search_index.reindex(session, market="TR")["news"] == 0 and [r.ref_id for r in _rows(session, "news")] == [untagged.id]
    assert runtime_settings.load_json(session, search_index.WATERMARK_KEY)["TR"]["news_pending_ai"] == []
    before = runtime_settings.load_json(session, search_index.WATERMARK_KEY)["US"]
    assert search_index.reindex(session, market="TR", full=True)["disclosure"] == 26 and runtime_settings.load_json(session, search_index.WATERMARK_KEY)["US"] == before  # US untouched
    full = search_index.reindex(session, full=True)
    assert full == {"disclosure": 36, "filing": 2, "news": 1, "note": 1} and len(_rows(session)) == sum(full.values())
    assert runtime_settings.load_json(session, search_index.WATERMARK_KEY)["US"]["filing_id"] == session.scalar(select(SecFiling.id).order_by(SecFiling.id.desc()))


def test_scheduler_tail_indexes_and_never_breaks_the_job(session, pipeline_run, monkeypatch, caplog):
    from instilens import scheduler

    scheduler.reindex(session)
    assert len(_rows(session, "disclosure")) == 25
    monkeypatch.setattr(search_index, "reindex", lambda s: 1 / 0)
    with caplog.at_level("WARNING", logger="instilens.scheduler"):
        scheduler.reindex(session)
    assert any("search index failed" in r.getMessage() for r in caplog.records)
    session.commit()  # the savepoint rolled back, the job's own transaction is intact
    assert len(_rows(session, "disclosure")) == 25


def test_indexer_text_helpers():
    assert search_index.clean_text("<p>Fon &amp; <b>pay</b></p>\n\n\n\n x   y ") == "Fon & pay\n\nx y"
    assert search_index.clean_text("Yönetim «temettü» dedi «") == 'Yönetim "temettü" dedi "'  # a document's own guillemets are not highlight marks
    assert search_index._num(46526835, "TR") == "46.526.835" and search_index._num("12.50", "TR") == "12,5" and search_index._num(-1500.25, "US") == "-1,500.25"
    assert search_index._num("n/a", "TR") == "n/a" and search_index._num(0, "US") == "0"


# --- SQLite LIKE engine -------------------------------------------------------------------------


def _indexed(session) -> None:
    _news(session, "Aselsan savunma ihracatında rekor kırdı", symbols=["ASELS"], ai={"summary_tr": "Aselsan ihracat rekoru kırdı."})
    _news(session, "SASA'da fon çıkışı sürüyor", symbols=["SASA"], when=datetime(2026, 9, 13, 10, 0))
    _news(session, "Endeks %2 yükseldi, bankalar öne çıktı", when=datetime(2026, 9, 13, 11, 0))
    _news(session, "US futures flat before the Fed", market="US", when=datetime(2026, 9, 13, 12, 0))
    session.add(AiNote(kind="STOCK_ASSESSMENT", market_code="TR", subject="ASELS", as_of=AS_OF, lang="tr", content="Üç fon ASELS pozisyonunu artırdı; Aselsan ihracat haberi ile aynı güne denk geldi.",
                       data={"headline": "ASELS: 3 fon artırdı", "highlights": [], "watch": []}, model="test"))
    session.commit()
    search_index.reindex(session)
    session.commit()


def test_like_search_ranks_filters_snippets_and_escapes(session, pipeline_run):
    _indexed(session)
    out = search.text_search(session, "TR", "aselsan", None, 20)
    assert set(out) == {"q", "market", "total", "hits"} and out["q"] == "aselsan" and out["market"] == "TR"
    assert out["total"] == 2 and [h["kind"] for h in out["hits"]] == ["news", "note"]  # a title hit outranks a body hit
    hit = out["hits"][0]
    assert set(hit) == {"kind", "id", "title", "snippet", "date", "symbols", "source", "url", "link", "superseded", "score"}
    assert hit["snippet"].startswith("«Aselsan» ihracat rekoru kırdı.") and hit["score"] > out["hits"][1]["score"] > 0 and hit["superseded"] is False
    assert "«Aselsan»" in out["hits"][1]["snippet"] and out["hits"][1]["source"] == "InstiLens AI" and out["hits"][1]["url"] is None
    assert all(len(h["snippet"]) <= search.SNIPPET_MAX for h in out["hits"])

    # Every word must match (AND), wherever it sits; the case of the query does not matter; ties break newest first.
    both = search.text_search(session, "TR", "ASELS fon", None, 50)
    assert both["hits"][0]["kind"] == "note" and both["total"] == len(both["hits"]) > 5  # "ASELS" in the note's title, "fon" in its body; the reports carry both in the body
    for h in both["hits"]:
        row = session.scalar(select(SearchableText).where(SearchableText.kind == h["kind"], SearchableText.ref_id == h["id"]))
        assert all(w in (row.title + row.body).lower() for w in ("asels", "fon"))
    reports = search.text_search(session, "TR", "portföy dağılım raporu", ["disclosure"], 50)["hits"]
    order = [(r["score"], r["date"]) for r in reports]
    assert len(reports) == 20 and order == sorted(order, key=lambda t: (-t[0], -date.fromisoformat(t[1]).toordinal())) and len({d for _, d in order}) == 4
    assert search.text_search(session, "TR", "aselsan fed", None, 20)["hits"] == []
    # -word excludes; a query with nothing but exclusions matches nothing (Postgres would otherwise match every other row).
    fon, fon_not = search.text_search(session, "TR", "fon", None, 50), search.text_search(session, "TR", "fon -aselsan", None, 50)
    assert fon["total"] > fon_not["total"] > 0 and fon_not["total"] == len(fon_not["hits"])
    for h in fon_not["hits"]:
        row = session.scalar(select(SearchableText).where(SearchableText.kind == h["kind"], SearchableText.ref_id == h["id"]))
        assert "aselsan" not in (row.title + row.body).lower()
    assert search.text_search(session, "TR", "-aselsan", None, 20) == {"q": "-aselsan", "market": "TR", "total": 0, "hits": []}
    assert search.text_search(session, "TR", "-fon -aselsan OR", None, 20)["total"] == 0
    # A superseded notice ranks after every live hit however well it matches, and says so.
    anele = search.text_search(session, "TR", "ANELE", ["disclosure"], 20)["hits"]
    assert [h["superseded"] for h in anele] == [False, True] and anele[1]["title"].startswith("Düzeltildi — ") and anele[1]["score"] >= anele[0]["score"]
    # kinds narrow, the other market is invisible, unknown kinds are refused, too-short queries return nothing.
    assert [h["kind"] for h in search.text_search(session, "TR", "aselsan", ["note"], 20)["hits"]] == ["note"]
    assert search.text_search(session, "TR", "futures", None, 20)["total"] == 0 and search.text_search(session, "US", "futures", None, 20)["total"] == 1
    with pytest.raises(ValueError):
        search.parse_kinds("news,bogus")
    assert search.parse_kinds(" News, note ,news") == ["news", "note"] and search.parse_kinds(None) == list(search_index.KINDS)
    assert search.text_search(session, "TR", "a", None, 20) == {"q": "a", "market": "TR", "total": 0, "hits": []}
    # LIKE wildcards in the query are literal characters, not patterns.
    assert search.text_search(session, "TR", "%%", None, 20)["total"] == 0 and search.text_search(session, "TR", "a_a", None, 20)["total"] == 0
    percent = search.text_search(session, "TR", "%2", None, 50)
    assert percent["hits"][0]["title"].startswith("Endeks %2") and all("«%2»" in h["snippet"] for h in percent["hits"])  # the headline and the reports' %2x weights
    # Snippet on the KAP figures: the window sits around the match, marks balanced, the limit kept even with many matches.
    hit = search.text_search(session, "TR", "1608450", ["disclosure"], 5)["hits"][0]
    assert hit["snippet"].startswith("KAP #«1608450»") and hit["link"] == "/stocks/ANELE" and hit["kind"] == "disclosure"
    many = search.text_search(session, "TR", "ASELS adet", ["disclosure"], 5)["hits"][0]["snippet"]  # a portfolio report: long body, several marks
    assert len(many) <= search.SNIPPET_MAX and many.count("«") == many.count("»") >= 2 and many.endswith("…")
    only_title = search.text_search(session, "TR", "ASELS", ["disclosure"], 50)["hits"]
    assert all("«ASELS»" in h["snippet"] for h in only_title) and any(h["title"].startswith("Pay Alım Satım Bildirimi · ASELS") for h in only_title)


def test_query_tokens_and_snippet_builder():
    assert search.tokens(' "pay alım" -satım OR  Aselsan  aselsan') == ["pay", "alım", "Aselsan", "aselsan"]
    assert search.tokens(' "pay alım" -satım -"kar" +fon', excluded=True) == ["satım", "kar"] and search.tokens("   ") == [] and search.tokens("-x -y") == []
    text = "word " * 80 + "needle in the middle " + "word " * 80
    snippet = search.snippet_for(text, ["needle", "middle"])
    assert snippet.startswith("…") and snippet.endswith("…") and "«needle» in the «middle»" in snippet and len(snippet) <= search.SNIPPET_MAX
    assert search.snippet_for("short text", ["nothing"]) == "short text"
    assert search.snippet_for("a" * 300, ["aaa"]).count("«") >= 1 and len(search.snippet_for("a" * 300, ["aaa"])) <= search.SNIPPET_MAX
    trimmed = search._trim("«" + "x" * 300)
    assert len(trimmed) <= search.SNIPPET_MAX and trimmed.count("«") == trimmed.count("»") and trimmed.endswith("…")


# --- route + tool -------------------------------------------------------------------------------


def _client(session):
    from instilens.api import deps
    from instilens.api.main import app

    app.dependency_overrides[deps.get_session] = lambda: session
    c = TestClient(app)
    token = auth.issue_token(auth.register(session, "s@example.com", "password123", "S"))
    c.headers["authorization"] = f"Bearer {token}"
    return c


def test_text_search_route_validation_and_shape(session, pipeline_run):
    _indexed(session)
    c = _client(session)
    assert TestClient(c.app).get("/api/v1/search/text", params={"q": "aselsan"}).status_code == 401
    assert c.get("/api/v1/search/text").status_code == 422
    assert c.get("/api/v1/search/text", params={"q": "a"}).status_code == 422
    assert c.get("/api/v1/search/text", params={"q": "x" * 129}).status_code == 422
    assert c.get("/api/v1/search/text", params={"q": "aselsan", "limit": 0}).status_code == 422
    assert c.get("/api/v1/search/text", params={"q": "aselsan", "limit": 51}).status_code == 422
    assert c.get("/api/v1/search/text", params={"q": "aselsan", "market": "XX"}).status_code == 422
    bad = c.get("/api/v1/search/text", params={"q": "aselsan", "kinds": "news,bogus"})
    assert bad.status_code == 422 and "bogus" in bad.json()["detail"]

    r = c.get("/api/v1/search/text", params={"q": "aselsan", "market": "TR", "limit": 1})
    assert r.status_code == 200
    body = r.json()
    assert body["q"] == "aselsan" and body["market"] == "TR" and body["total"] == 2 and len(body["hits"]) == 1
    hit = body["hits"][0]
    assert set(hit) == {"kind", "id", "title", "snippet", "date", "symbols", "source", "url", "link", "superseded", "score"}
    assert hit["kind"] == "news" and isinstance(hit["id"], int) and hit["date"] == "2026-09-12" and hit["symbols"] == ["ASELS"] and hit["link"] == "/stocks/ASELS"
    assert c.get("/api/v1/search/text", params={"q": "aselsan", "kinds": "note"}).json()["hits"][0]["kind"] == "note"
    assert c.get("/api/v1/search/text", params={"q": "aselsan", "kinds": "filing,disclosure"}).json() == {"q": "aselsan", "market": "TR", "total": 0, "hits": []}
    assert c.get("/api/v1/search/text", params={"q": "aselsan", "market": "US"}).json()["total"] == 0


def test_search_texts_tool_returns_compact_hits(session, pipeline_run):
    _indexed(session)
    tools = {fn.__name__: fn for fn in build_tools(session, "TR")}
    hits = json.loads(tools["search_texts"]("aselsan"))
    assert [h["kind"] for h in hits] == ["news", "note"]
    assert set(hits[0]) == {"kind", "id", "title", "snippet", "date", "symbols", "source", "url", "link", "superseded"}  # no score for the model
    assert "«Aselsan»" in hits[0]["snippet"] and hits[0]["url"].startswith("https://") and hits[1]["source"] == "InstiLens AI"
    assert json.loads(tools["search_texts"]("aselsan", kinds=["note"], limit=99)) == hits[1:]
    assert json.loads(tools["search_texts"]("nothing-like-this")) == []
    assert json.loads(build_tools(session, "US")[-1]("aselsan")) == []
    # Both engines see the same contract: Claude's beta_tool schema and the local engine's OpenAI schema carry the kinds enum.
    claude = beta_tool(tools["search_texts"]).to_dict()
    assert claude["name"] == "search_texts" and claude["input_schema"]["required"] == ["query"]
    assert claude["input_schema"]["properties"]["kinds"]["anyOf"][0]["items"]["enum"] == list(search_index.KINDS)
    local = function_schema(tools["search_texts"])["function"]
    assert local["parameters"]["required"] == ["query"] and local["parameters"]["properties"]["kinds"]["items"]["enum"] == list(search_index.KINDS)
    assert "never claim a document says something that is not in the snippet" in local["description"] and "superseded" in local["description"]
    old = json.loads(tools["search_texts"]("1608319"))  # the correction names that KAP id; the notice it replaced carries it
    assert [h["superseded"] for h in old] == [False, True] and old[0]["title"].startswith("Düzeltme — ") and old[1]["title"].startswith("Düzeltildi — ")


def test_admin_news_reapply_refreshes_the_index(session, pipeline_run):
    """Rules rewrite tags and delete non-finance headlines without a timestamp: the admin route re-indexes the window."""
    _indexed(session)
    noise = _news(session, "Fenerbahçe derbiyi kazandı", url="https://news.example/spor/derbi", when=datetime.now(UTC).replace(tzinfo=None) - timedelta(days=1))
    session.commit()
    search_index.reindex(session, market="TR")
    session.commit()
    assert search.text_search(session, "TR", "derbi", None, 20)["total"] == 1
    c = _client(session)
    root = auth.register(session, "root@example.com", "correct-horse-1", "Root")
    root.role = "ADMIN"
    session.flush()
    r = c.post("/api/v1/admin/news/reapply", headers={"authorization": f"Bearer {auth.issue_token(root)}"})
    assert r.status_code == 200 and set(r.json()) == {"TR", "US"}
    assert session.get(NewsItem, noise.id) is None and search.text_search(session, "TR", "derbi", None, 20)["total"] == 0


# --- migration ---------------------------------------------------------------------------------


def test_migration_creates_searchable_texts_on_scratch_sqlite(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine, inspect, text

    from instilens.config import BACKEND_ROOT, settings

    url = f"sqlite:///{tmp_path / 'scratch.db'}"
    monkeypatch.setattr(settings, "database_url", url)
    cfg = Config()
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    command.upgrade(cfg, "head")
    insp = inspect(create_engine(url))
    assert {c["name"] for c in insp.get_columns("searchable_texts")} == {"id", "kind", "ref_id", "market_code", "symbols", "title", "body", "date", "source", "url", "link", "superseded", "updated_at"}  # no tsv off Postgres
    assert {"kind", "ref_id"} in [set(u["column_names"]) for u in insp.get_unique_constraints("searchable_texts")]
    assert "ix_searchable_texts_market_date" in {i["name"] for i in insp.get_indexes("searchable_texts")}
    with create_engine(url).begin() as conn:
        assert conn.execute(text("SELECT version_num FROM alembic_version")).scalar() == "b4c5d6e7f8a9"  # the current head (KAP insiders: three columns on insider_transactions)
    command.downgrade(cfg, "e1f2a3b4c5d6")
    assert "searchable_texts" not in inspect(create_engine(url)).get_table_names()
