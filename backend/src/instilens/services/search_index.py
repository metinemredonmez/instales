"""Full-text search index: the documents the product already stores, flattened into `searchable_texts`.

Four kinds, each one row per source row, in the document's own language (KAP in Turkish, SEC in English, an AI
note in the language it was written in):
  disclosure  KAP share-transaction notices and portfolio reports, SEC 13F and Form 4 — the title and the textual
              fields of the parsed payload (member, funds, rows, footnotes, remarks), never the raw HTML or PDF;
  filing      the EDGAR submissions index (8-K/10-K/10-Q/4) — form, 8-K item titles, issuer;
  news        a headline plus its AI summary (summary_tr) and rule tags;
  note        an AI note — headline, text, highlights, what to watch.
Bodies are plain text capped at BODY_MAX characters; a document's own «» quotation marks become " so they cannot be
read as the snippet highlight marks `services/search` writes. `services/search` queries the rows (tsvector on
Postgres, LIKE on SQLite); the ORM row is `domain.models.SearchableText`.

Incremental: `reindex()` only touches source rows written since the last run. The watermark lives in app_settings
under `_search_index` as JSON, per market: `since` (when the last run started; `ingested_at` / `fetched_at` /
`created_at` of disclosures, news and notes are compared against it, SLACK earlier), `filing_id` (the highest
listing row indexed — filings are insert-only and carry no timestamp) and `news_pending_ai` (headlines indexed
before the AI pass tagged them, re-read on every run until their summary exists). Re-reading is cheap because a row
whose text did not change is not written again (no UPDATE, `updated_at` untouched) — so SLACK can be wide enough to
cover an ingest transaction that was still open when a run started (timestamps are set at flush, before the
commit; the scheduler runs news_pull's reindex next to a KAP or SEC ingest). A correction also re-indexes the
disclosure it supersedes, so its row is titled and flagged as superseded and ranks after live rows. Headlines
deleted at the source (news_rules.reapply_all) leave the index on the next news pass. The scheduler calls it at the
tail of compute, news_pull and briefs; `instilens reindex [--full]` runs it by hand (--full drops and rebuilds
every row).
"""

from __future__ import annotations

import html
import logging
import re
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.orm import Session

from instilens.domain.enums import DisclosureKind
from instilens.domain.models import (
    AiNote,
    Disclosure,
    Instrument,
    NewsItem,
    SearchableText,
    SecFiling,
)
from instilens.services import runtime_settings

log = logging.getLogger("instilens.search_index")

KINDS = ("disclosure", "filing", "news", "note")
MARKETS = ("TR", "US")
BODY_MAX = 20_000
TITLE_MAX = 512
SYMBOLS_MAX = 60  # symbols kept per row: a 13F lists thousands of positions, the hit shows the largest
AI_SOURCE = "InstiLens AI"
WATERMARK_KEY = "_search_index"
PENDING_AI_MAX = 2000  # news ids waiting for their AI summary; older ones are dropped after PENDING_AI_DAYS
PENDING_AI_DAYS = 14
SLACK = timedelta(hours=2)  # how far behind the last run's start the incremental pass reads: longer than any ingest job (see reindex)
BATCH = 500

# EDGAR form and 8-K item titles (the listing carries codes only; the titles are what people search for).
FORM_LABELS = {"4": "insider transaction report (Form 4)", "4/A": "amended insider transaction report (Form 4/A)",
               "8-K": "current report", "10-K": "annual report", "10-Q": "quarterly report"}
ITEM_TITLES = {
    "1.01": "Entry into a Material Definitive Agreement", "1.02": "Termination of a Material Definitive Agreement",
    "1.03": "Bankruptcy or Receivership", "1.04": "Mine Safety", "1.05": "Material Cybersecurity Incidents",
    "2.01": "Completion of Acquisition or Disposition of Assets", "2.02": "Results of Operations and Financial Condition",
    "2.03": "Creation of a Direct Financial Obligation", "2.04": "Triggering Events That Accelerate a Financial Obligation",
    "2.05": "Costs Associated with Exit or Disposal Activities", "2.06": "Material Impairments",
    "3.01": "Notice of Delisting or Failure to Satisfy a Continued Listing Rule", "3.02": "Unregistered Sales of Equity Securities",
    "3.03": "Material Modification to Rights of Security Holders", "4.01": "Changes in Registrant's Certifying Accountant",
    "4.02": "Non-Reliance on Previously Issued Financial Statements", "5.01": "Changes in Control of Registrant",
    "5.02": "Departure or Election of Directors or Officers; Compensatory Arrangements", "5.03": "Amendments to Articles of Incorporation or Bylaws; Change in Fiscal Year",
    "5.04": "Temporary Suspension of Trading Under Employee Benefit Plans", "5.05": "Amendments to the Code of Ethics",
    "5.06": "Change in Shell Company Status", "5.07": "Submission of Matters to a Vote of Security Holders", "5.08": "Shareholder Nominations",
    "6.01": "ABS Informational and Computational Material", "7.01": "Regulation FD Disclosure", "8.01": "Other Events",
    "9.01": "Financial Statements and Exhibits",
}
# Form 4 transaction codes as the filing means them (see ingestion/sec/form4).
CODE_LABELS = {"P": "open-market or private purchase", "S": "open-market or private sale", "A": "grant or award",
               "M": "option exercise or RSU settlement", "F": "shares withheld for tax", "G": "gift", "D": "disposition to the issuer",
               "C": "conversion", "X": "exercise of an in-the-money derivative", "J": "other (see footnotes)", "W": "will or inheritance"}

_TAGS = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t\r\f\v]+")
_BLANKS = re.compile(r"\n\s*\n+")


# --------------------------------------------------------------------------- text helpers


def clean_text(value: Any) -> str:
    """Plain text: tags stripped, entities decoded, «» quotation marks turned into " (they are the snippet's highlight
    marks), runs of spaces collapsed, at most one blank line in a row."""
    if value is None:
        return ""
    text = html.unescape(_TAGS.sub(" ", str(value))).replace("«", '"').replace("»", '"')
    text = "\n".join(_WS.sub(" ", line).strip() for line in text.splitlines())
    return _BLANKS.sub("\n\n", text).strip()


def _num(value: Any, market: str) -> str:
    """A quantity or amount the way the document's market writes it: 46.526.835 / 12,5 in Turkey, 46,526,835 / 12.5
    in the US. Anything that is not a number comes back as its cleaned text."""
    try:
        d = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return clean_text(value)
    sign = "-" if d < 0 else ""
    whole, _, frac = f"{abs(d):f}".partition(".")
    frac = frac.rstrip("0")
    grouped = f"{int(whole):,}"
    if market == "TR":
        return sign + grouped.replace(",", ".") + (f",{frac}" if frac else "")
    return sign + grouped + (f".{frac}" if frac else "")


def _sym(value: Any) -> str:
    return clean_text(value).upper()


def _unique(items: list[str]) -> list[str]:
    seen: list[str] = []
    for s in items:
        if s and s not in seen:
            seen.append(s)
    return seen[:SYMBOLS_MAX]


def _naive(dt: datetime | None) -> datetime | None:
    """Watermarks compare against naive UTC columns (SQLite drops the offset, Postgres columns are timestamp without
    time zone): normalise the aware defaults the models set."""
    if dt is None:
        return None
    return dt.astimezone(UTC).replace(tzinfo=None) if dt.tzinfo is not None else dt


def _row(*, kind: str, ref_id: int, market: str, symbols: list[str], title: str, lines: list[str], on: date, source: str,
         url: str | None, link: str | None, superseded: bool = False) -> dict:
    body = clean_text("\n".join(line for line in lines if line))
    return {"kind": kind, "ref_id": ref_id, "market_code": market, "symbols": _unique(symbols), "title": clean_text(title)[:TITLE_MAX],
            "body": body[:BODY_MAX], "date": on, "source": source, "url": (url or None) and str(url)[:512], "link": (link or None) and str(link)[:128],
            "superseded": superseded}


# The title of a disclosure a later correction replaced, so a hit list and the AI tool read it as such.
SUPERSEDED_TITLE = {"TR": "Düzeltildi — ", "US": "Superseded — "}


# --------------------------------------------------------------------------- extractors: disclosures


def _kap_share_transaction(d: Disclosure, p: dict) -> dict:
    symbol = _sym(p.get("subject_symbol"))
    member = clean_text(p.get("member_name"))
    correction = bool(p.get("is_correction") or p.get("amends_source_id"))
    funds = [_sym(c) for c in (p.get("related_fund_codes") or []) if clean_text(c)]
    companies = [_sym(c) for c in (p.get("related_companies") or []) if clean_text(c)]
    lines = [f"KAP #{d.source_id} · {member}", f"Şirket: {clean_text(p['subject_name'])} ({symbol})" if p.get("subject_name") else f"Pay: {symbol}"]
    if companies:
        lines.append("İlgili şirketler: " + ", ".join(companies))
    if funds:
        lines.append("İlgili fonlar: " + ", ".join(funds))
    for r in p.get("rows") or []:
        side = {"ALIS": "Alış", "SATIS": "Satış"}.get(str(r.get("side")), clean_text(r.get("side")))
        line = f"{clean_text(r.get('transaction_date'))} {side} {_num(r.get('nominal'), 'TR')} nominal"
        if r.get("price") not in (None, ""):
            line += f" · fiyat {_num(r['price'], 'TR')} TL"
        lines.append(line)
    if p.get("ownership_before_pct") is not None or p.get("ownership_after_pct") is not None:
        before, after = p.get("ownership_before_pct"), p.get("ownership_after_pct")
        lines.append(f"Sermayedeki pay: %{_num(before, 'TR') if before is not None else '—'} → %{_num(after, 'TR') if after is not None else '—'}")
    if p.get("amends_source_id"):
        lines.append(f"Düzeltilen bildirim: KAP #{clean_text(p['amends_source_id'])}")
    if d.is_superseded:
        lines.append("Bu bildirim daha sonra yapılan bir düzeltme bildirimi ile geçersiz kılındı.")
    title = f"{SUPERSEDED_TITLE['TR'] if d.is_superseded else ''}{'Düzeltme — ' if correction else ''}Pay Alım Satım Bildirimi · {symbol} · {member}"
    return _row(kind="disclosure", ref_id=d.id, market=d.market_code, symbols=[symbol, *companies], title=title, lines=lines,
                on=d.published_at.date(), source=d.source, url=d.raw_uri, link=f"/stocks/{symbol}" if symbol else None, superseded=bool(d.is_superseded))


def _kap_portfolio_report(d: Disclosure, p: dict) -> dict:
    code = _sym(p.get("fund_code"))
    name = clean_text(p.get("fund_name"))
    lines = [f"KAP #{d.source_id} · Fon: {name or code} ({code})"]
    if p.get("member_name"):
        lines.append(f"Kurucu / yönetici: {clean_text(p['member_name'])}")
    lines.append(f"Rapor tarihi: {clean_text(p.get('as_of'))}")
    if p.get("total_value") is not None:
        lines.append(f"Toplam portföy değeri: {_num(p['total_value'], 'TR')} TL")
    holdings = [h for h in (p.get("holdings") or []) if isinstance(h, dict)]
    symbols = [_sym(h.get("symbol")) for h in holdings]
    if holdings:
        lines.append("Pay senetleri:")
    for h in holdings:
        line = f"{_sym(h.get('symbol'))}: {_num(h.get('quantity'), 'TR')} adet"
        if h.get("market_value") is not None:
            line += f" · {_num(h['market_value'], 'TR')} TL"
        if h.get("weight_pct") is not None:
            line += f" · %{_num(h['weight_pct'], 'TR')}"
        lines.append(line)
    if d.is_superseded:
        lines.append("Bu rapor daha sonra yapılan bir düzeltme ile geçersiz kılındı.")
    title = (SUPERSEDED_TITLE["TR"] if d.is_superseded else "") + f"Fon Portföy Dağılım Raporu · {code}" + (f" · {name}" if name else "")
    return _row(kind="disclosure", ref_id=d.id, market=d.market_code, symbols=symbols, title=title, lines=lines,
                on=d.published_at.date(), source=d.source, url=d.raw_uri, link=f"/funds/{code}" if code else None, superseded=bool(d.is_superseded))


def _sec_13f(session: Session, d: Disclosure, p: dict) -> dict:
    filer = clean_text(p.get("filer_name"))
    cik = clean_text(p.get("cik"))
    code = f"CIK{int(cik)}" if cik.isdigit() else cik
    form = "13F-HR/A" if p.get("amendment") else "13F-HR"
    lines = [f"Accession {d.source_id} · Filer: {filer} (CIK {cik})", f"Period of report: {clean_text(p.get('period'))}"]
    if p.get("amendment_type"):
        lines.append(f"Amendment type: {clean_text(p['amendment_type'])}")
    if p.get("amends_source_id"):
        lines.append(f"Amends accession {clean_text(p['amends_source_id'])}")
    holdings = sorted((h for h in (p.get("holdings") or []) if isinstance(h, dict)), key=lambda h: -(h.get("value_usd") or 0))
    # The largest positions' tickers, where the CUSIP map knows them (an unmapped CUSIP is not a symbol).
    cusips = [clean_text(h.get("cusip")) for h in holdings[:SYMBOLS_MAX]]
    known = {c: sym for c, sym in session.execute(select(Instrument.cusip, Instrument.symbol).where(Instrument.cusip.in_(cusips)))} if cusips else {}
    symbols = [known[c] for c in cusips if c in known and not known[c].startswith("CUSIP:")]
    if holdings:
        lines.append("Holdings (shares only, aggregated per CUSIP, largest first):")
    for h in holdings:
        line = f"{clean_text(h.get('issuer')) or 'unnamed issuer'} (CUSIP {clean_text(h.get('cusip'))}): {_num(h.get('quantity'), 'US')} sh"
        if h.get("value_usd") is not None:
            line += f" · ${_num(h['value_usd'], 'US')}"
        if h.get("weight_pct") is not None:
            line += f" · {_num(h['weight_pct'], 'US')} %"
        lines.append(line)
    if d.is_superseded:
        lines.append("Superseded by a later amendment.")
    title = f"{SUPERSEDED_TITLE['US'] if d.is_superseded else ''}{form} · {filer} · period {clean_text(p.get('period'))}"
    return _row(kind="disclosure", ref_id=d.id, market=d.market_code, symbols=symbols, title=title, lines=lines,
                on=d.published_at.date(), source=d.source, url=d.raw_uri, link=f"/funds/{code}" if code else None, superseded=bool(d.is_superseded))


def _form4_owner(o: dict) -> str:
    roles = [r for r in ("director", "officer", "ten_percent_owner", "other") if o.get(r)]
    words = {"director": "director", "officer": "officer", "ten_percent_owner": "10% owner", "other": clean_text(o.get("other_text")) or "other"}
    text = clean_text(o.get("name")) or "unnamed owner"
    if roles:
        text += " — " + ", ".join(words[r] for r in roles)
    if o.get("officer_title"):
        text += f" ({clean_text(o['officer_title'])})"
    return text


def _form4_transaction(t: dict) -> str:
    code = clean_text(t.get("code"))
    line = f"{clean_text(t.get('date'))} code {code or '?'}"
    if code in CODE_LABELS:
        line += f" ({CODE_LABELS[code]})"
    line += f": {'acquired' if t.get('acquired_disposed') == 'A' else 'disposed of' if t.get('acquired_disposed') == 'D' else ''} {_num(t.get('shares'), 'US')} {clean_text(t.get('security_title'))}".rstrip()
    if t.get("price") not in (None, ""):
        line += f" @ {_num(t['price'], 'US')}"
    if t.get("post_shares") not in (None, ""):
        line += f", {_num(t['post_shares'], 'US')} held after"
    nature = {"D": "direct", "I": "indirect"}.get(str(t.get("ownership_nature")), "")
    if nature:
        line += f" ({nature}" + (f": {clean_text(t['nature_of_ownership'])}" if t.get("nature_of_ownership") else "") + ")"
    if t.get("derivative"):
        line += " [derivative table"
        if t.get("underlying_title"):
            line += f": {_num(t.get('underlying_shares'), 'US')} {clean_text(t['underlying_title'])}".rstrip()
        if t.get("exercise_price") not in (None, ""):
            line += f", exercise price {_num(t['exercise_price'], 'US')}"
        line += "]"
    return line


def _sec_form4(d: Disclosure, p: dict) -> dict:
    issuer = p.get("issuer") if isinstance(p.get("issuer"), dict) else {}
    symbol = _sym(issuer.get("symbol"))
    name = clean_text(issuer.get("name"))
    owners = [o for o in (p.get("reporting_owners") or []) if isinstance(o, dict)]
    form = clean_text(p.get("document_type")) or "4"
    lines = [f"Accession {d.source_id} · Issuer: {name} ({symbol}, CIK {clean_text(issuer.get('cik'))})"]
    if p.get("period_of_report"):
        lines.append(f"Period of report: {clean_text(p['period_of_report'])}")
    lines += [f"Reporting owner: {_form4_owner(o)}" for o in owners]
    if p.get("aff_10b5_one"):
        lines.append("Transactions made pursuant to a Rule 10b5-1 trading plan.")
    rows = [t for t in (p.get("non_derivative_transactions") or []) + (p.get("derivative_transactions") or []) if isinstance(t, dict)]
    if rows:
        lines.append("Transactions:")
    lines += [_form4_transaction(t) for t in rows]
    footnotes = p.get("footnotes") if isinstance(p.get("footnotes"), dict) else {}
    if footnotes:
        lines.append("Footnotes:")
        lines += [f"{clean_text(k)}: {clean_text(v)}" for k, v in footnotes.items()]
    if p.get("remarks"):
        lines.append(f"Remarks: {clean_text(p['remarks'])}")
    if d.is_superseded:
        lines.append("Superseded by a later amendment (Form 4/A).")
    who = ", ".join(clean_text(o.get("name")) for o in owners if clean_text(o.get("name")))
    title = (SUPERSEDED_TITLE["US"] if d.is_superseded else "") + f"Form {form} · {name or symbol}" + (f" ({symbol})" if symbol and name else "") + (f" · {who}" if who else "")
    return _row(kind="disclosure", ref_id=d.id, market=d.market_code, symbols=[symbol], title=title, lines=lines,
                on=d.published_at.date(), source=d.source, url=d.raw_uri, link=f"/stocks/{symbol}" if symbol else None, superseded=bool(d.is_superseded))


def disclosure_row(session: Session, d: Disclosure) -> dict | None:
    """The index row of one disclosure by its kind; None for a kind the index does not know."""
    p = d.payload if isinstance(d.payload, dict) else {}
    if d.kind == DisclosureKind.KAP_SHARE_TRANSACTION:
        return _kap_share_transaction(d, p)
    if d.kind == DisclosureKind.KAP_PORTFOLIO_REPORT:
        return _kap_portfolio_report(d, p)
    if d.kind == DisclosureKind.SEC_13F:
        return _sec_13f(session, d, p)
    if d.kind == DisclosureKind.SEC_FORM4:
        return _sec_form4(d, p)
    return None


# --------------------------------------------------------------------------- extractors: filings, news, notes


def filing_row(f: SecFiling, inst: Instrument) -> dict:
    label = FORM_LABELS.get(f.form, "filing")
    lines = [f"Filed {f.filed_at.isoformat()} by {inst.name} ({inst.symbol}), form {f.form}: {label}."]
    if f.period:
        lines.append(f"Period of report: {f.period.isoformat()}")
    items = [str(i) for i in (f.items or [])]
    if items:
        lines.append("Items: " + "; ".join(f"{i} {ITEM_TITLES[i]}" if i in ITEM_TITLES else i for i in items))
    lines.append(f"Accession {f.accession}" + (f" · primary document {f.primary_document}" if f.primary_document else ""))
    return _row(kind="filing", ref_id=f.id, market=inst.market_code, symbols=[inst.symbol], title=f"{f.form} {label} · {inst.symbol} · {inst.name}",
                lines=lines, on=f.filed_at, source="SEC", url=f.url, link=f"/stocks/{inst.symbol}")


def news_row(n: NewsItem) -> dict:
    ai = n.ai if isinstance(n.ai, dict) else {}
    symbols = [_sym(s) for s in (n.symbols or [])]
    lines = [clean_text(ai.get("summary_tr"))]
    if ai.get("sector"):
        lines.append(f"Sektör: {clean_text(ai['sector'])}")
    if n.tags:
        lines.append("Etiketler: " + ", ".join(clean_text(t) for t in n.tags))
    lines.append(f"Kaynak: {clean_text(n.source)}")
    return _row(kind="news", ref_id=n.id, market=n.market_code, symbols=symbols, title=n.title, lines=lines, on=n.published_at.date(),
                source=n.source, url=n.url, link=f"/stocks/{symbols[0]}" if symbols else None)


def note_row(note: AiNote) -> dict:
    data = note.data if isinstance(note.data, dict) else {}
    stock = note.kind == "STOCK_ASSESSMENT"
    headline = clean_text(data.get("headline"))
    if not headline:
        kind_label = {"STOCK_ASSESSMENT": ("Kurumsal akış değerlendirmesi", "Institutional flow assessment"),
                      "DAILY_BRIEF": ("Günlük brif", "Daily brief")}.get(note.kind, (note.kind, note.kind))
        headline = f"{kind_label[1] if note.lang == 'en' else kind_label[0]} · {note.subject if stock else note.market_code} · {note.as_of.isoformat()}"
    lines = [clean_text(note.content)]
    highlights = [clean_text(h) for h in (data.get("highlights") or []) if clean_text(h)]
    watch = [clean_text(w) for w in (data.get("watch") or []) if clean_text(w)]
    if highlights:
        lines.append(("Highlights: " if note.lang == "en" else "Öne çıkanlar: ") + " · ".join(highlights))
    if watch:
        lines.append(("Watch: " if note.lang == "en" else "İzlenecekler: ") + " · ".join(watch))
    if data.get("confidence_note"):
        lines.append(clean_text(data["confidence_note"]))
    # No link: the stock page and the Radar show the *current* note, not this one — the snippet is where the text is,
    # the symbol chip leads to the stock page.
    return _row(kind="note", ref_id=note.id, market=note.market_code, symbols=[note.subject.upper()] if stock else [], title=headline, lines=lines,
                on=note.as_of, source=AI_SOURCE, url=None, link=None)


# --------------------------------------------------------------------------- upsert + watermark


def _upsert(session: Session, rows: list[dict]) -> int:
    """Write rows by (kind, ref_id): add the missing one, update the existing one whose fields differ — an unchanged
    row is left alone (no UPDATE, `updated_at` kept), which is what makes re-reading the slack window and the pending
    headlines cheap. Returns rows written (added or changed)."""
    written = 0
    now = datetime.now(UTC).replace(tzinfo=None)
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        by_kind: dict[str, list[int]] = {}
        for r in chunk:
            by_kind.setdefault(r["kind"], []).append(r["ref_id"])
        existing: dict[tuple[str, int], SearchableText] = {}
        for kind, ids in by_kind.items():
            for row in session.scalars(select(SearchableText).where(SearchableText.kind == kind, SearchableText.ref_id.in_(ids))):
                existing[(row.kind, row.ref_id)] = row
        for r in chunk:
            row = existing.get((r["kind"], r["ref_id"]))
            if row is None:
                session.add(SearchableText(**r, updated_at=now))
            elif any(getattr(row, k) != v for k, v in r.items()):
                for k, v in r.items():
                    setattr(row, k, v)
                row.updated_at = now
            else:
                continue
            written += 1
        session.flush()
    return written


def _index_disclosures(session: Session, market: str, since: datetime | None) -> int:
    """Disclosures are read in pages of BATCH ids and their payloads expired once flattened: a 13F payload is the
    whole information table, and a full rebuild must not hold every filing in memory at once."""
    stmt = select(Disclosure.id, Disclosure.supersedes_id).where(Disclosure.market_code == market)
    if since is not None:
        stmt = stmt.where(Disclosure.ingested_at >= since)
    pairs = session.execute(stmt.order_by(Disclosure.id)).all()
    ids = [i for i, _ in pairs]
    # A correction flips `is_superseded` on the disclosure it replaces without touching that row's timestamp.
    ids += sorted({parent for _, parent in pairs if parent} - set(ids))
    written = 0
    for start in range(0, len(ids), BATCH):
        page = session.scalars(select(Disclosure).where(Disclosure.id.in_(ids[start:start + BATCH])).order_by(Disclosure.id)).all()
        written += _upsert(session, [r for r in (disclosure_row(session, d) for d in page) if r is not None])
        for d in page:
            session.expire(d, ["payload"])
    return written


def _index_filings(session: Session, market: str, wm: dict, since: datetime | None) -> int:
    """Listing rows are insert-only and carry no timestamp: the watermark is the highest id indexed (`filing_id`).
    An explicit `since` also re-reads the rows filed on or after that day."""
    after_id = int(wm.get("filing_id") or 0)
    changed = SecFiling.id > after_id
    if since is not None:
        changed = changed | (SecFiling.filed_at >= since.date())
    stmt = select(SecFiling, Instrument).join(Instrument, Instrument.id == SecFiling.instrument_id).where(Instrument.market_code == market, changed)
    pairs = session.execute(stmt.order_by(SecFiling.id)).all()
    if pairs:
        wm["filing_id"] = max(after_id, max(f.id for f, _ in pairs))
    return _upsert(session, [filing_row(f, inst) for f, inst in pairs])


def _index_news(session: Session, market: str, wm: dict, since: datetime | None) -> int:
    """Headlines fetched since the watermark, plus the ones indexed before the AI pass tagged them (`news_pending_ai`):
    the summary is written later, by enrich(), without any timestamp on the row. Index rows whose headline is gone
    (news_rules.reapply_all deletes non-finance items) are dropped — the only kind whose source rows are deleted."""
    stmt = select(NewsItem).where(NewsItem.market_code == market)
    if since is not None:
        stmt = stmt.where(NewsItem.fetched_at >= since)
    items = session.scalars(stmt.order_by(NewsItem.id)).all()
    seen = {n.id for n in items}
    pending = [int(i) for i in (wm.get("news_pending_ai") or []) if int(i) not in seen]
    if pending:
        items += session.scalars(select(NewsItem).where(NewsItem.id.in_(pending))).all()
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=PENDING_AI_DAYS)
    still = sorted({n.id for n in items if n.ai is None and _naive(n.fetched_at) is not None and _naive(n.fetched_at) >= cutoff})
    wm["news_pending_ai"] = still[-PENDING_AI_MAX:]
    written = _upsert(session, [news_row(n) for n in items])
    gone = session.execute(delete(SearchableText).where(SearchableText.kind == "news", SearchableText.market_code == market,
                                                        ~exists().where(NewsItem.id == SearchableText.ref_id))).rowcount
    if gone:
        log.info("search index %s: dropped %s news rows whose headline is gone", market, gone)
    return written


def _index_notes(session: Session, market: str, since: datetime | None) -> int:
    stmt = select(AiNote).where(AiNote.market_code == market)
    if since is not None:
        stmt = stmt.where(AiNote.created_at >= since)  # a rewritten note (force refresh) gets a new created_at
    return _upsert(session, [note_row(n) for n in session.scalars(stmt.order_by(AiNote.id))])


def reindex(session: Session, market: str | None = None, since: datetime | None = None, *, full: bool = False) -> dict[str, int]:
    """Bring the index up to date. Default: only the source rows written since the last run of that market — the
    stored `since` is when that run started, read back SLACK earlier so a row whose ingest transaction was still open
    when a run started (its timestamp is set at flush, before the commit) is picked up by a later run; the rows the
    window re-reads are only written when their text differs. `since` replaces the watermark for this run (every
    kind); `full` drops the market's rows and rebuilds them all. Returns rows written (added or changed) per kind."""
    state = runtime_settings.load_json(session, WATERMARK_KEY)
    state = dict(state) if isinstance(state, dict) else {}  # a full rebuild resets only the markets it processes
    counts = dict.fromkeys(KINDS, 0)
    for m in [market] if market else MARKETS:
        wm = dict(state.get(m) or {}) if not full else {}
        started = datetime.now(UTC).replace(tzinfo=None)
        if full:
            session.execute(delete(SearchableText).where(SearchableText.market_code == m))
        if since is not None:
            cut = _naive(since)
        else:
            last = wm.get("since")
            cut = datetime.fromisoformat(last) - SLACK if last else None
        counts["disclosure"] += _index_disclosures(session, m, cut)
        counts["filing"] += _index_filings(session, m, wm, cut if since is not None else None)
        counts["news"] += _index_news(session, m, wm, cut)
        counts["note"] += _index_notes(session, m, cut)
        wm["since"] = started.isoformat()
        state[m] = wm
    runtime_settings.store_json(session, WATERMARK_KEY, state, actor="search_index")
    log.info("search index %s", counts)
    return counts
