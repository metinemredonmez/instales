"""Insider transactions per instrument — SEC Form 4 for US issuers (Faz 4), KAP "Pay Alım Satım Bildirimi" of persons
and shareholders for BIST — and the US issuer filings index (8-K / 10-K / 10-Q).

`refresh` walks the US issuer universe through `EdgarClient`: the submissions listing feeds `sec_filings`, every new
Form 4 / 4/A is fetched, parsed (`ingestion/sec/form4`) and stored as a Disclosure (kind SEC_FORM4, payload = the
parsed document) plus one `insider_transactions` row per transaction-table row — every stored number carries its
accession and filing URL. A 4/A supersedes its original the way a 13F-HR/A does (`Disclosure.supersedes_id`; the
old rows stay, flagged). The issuer (its CIK) is the unit of storage: share classes (GOOG / GOOGL, LEN / LEN-B) share
one listing and one set of Form 4s, stored once and read through the CIK by every class.

`refresh_kap` reads the KAP side through the public adapter's insider path (`ingestion/kap/public_adapter
.iter_fetch_insiders`, the same politeness caps as the PYŞ ingest and never the same disclosures): each filing is
one Disclosure (kind KAP_INSIDER_TRANSACTION, payload = the parsed page / SPK form) plus one row per day and side,
code P for ALIŞ and S for SATIŞ, keyed by the disclosure index. The PYŞ path is untouched: insider rows never enter
transaction_events or the scores. A "Düzeltme" supersedes the filing it names, or failing that the party's latest
live filing on the same stock, the way a 4/A does. A page whose party or numbers cannot be read is stored FAILED
with the reason, so it is visible and never fetched again.

`summary`, `transactions` and `filings` are the read models behind /stocks/{symbol}/insiders, /stocks/{symbol}/filings
and stock_detail, market-agnostic; `trades` feeds the cluster detector (`engine/insiders`). Descriptive throughout:
a row's transaction code is reported as filed (P open-market purchase, S sale, A grant, M exercise / RSU settlement,
F tax withholding …), never flattened into "buy" / "sell", and only P and S rows count as buyers / sellers. A company
trading its own shares (`roles = "issuer"`, a buyback or a treasury-share sale) is listed with that label and left
out of every count and of the cluster: it is not an insider purchase.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree

import httpx
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.enums import Confidence, DisclosureKind, Market, ParseStatus, Source
from instilens.domain.models import (
    Disclosure,
    InsiderTransaction,
    Instrument,
    PositionChange,
    SecFiling,
    WatchlistItem,
)
from instilens.domain.schemas import NormalizedInsiderFiling, RawDisclosure
from instilens.engine.insiders import (
    CLUSTER_WINDOW_DAYS,
    InsiderTrade,
    cluster,
    detect_insider_buy_cluster,
)
from instilens.engine.signals import DetectedSignal
from instilens.ingestion.kap.public_adapter import KapPublicAdapter
from instilens.ingestion.sec import tickers
from instilens.ingestion.sec.edgar_client import ARCHIVE, EdgarClient
from instilens.ingestion.sec.form4 import officer_title, parse_form4, primary_owner, roles_of
from instilens.parsing.kap_insider import ISSUER_ROLE, parse_insider_filing, party_key
from instilens.services.entities import EntityResolver

log = logging.getLogger("instilens.insiders")

SOURCE = EdgarClient.name  # "sec-edgar": what the API reports as the origin of every US row
KAP_SOURCE = "kap"  # … and of every TR row (the public KAP site, prototype adapter)
SOURCE_OF = {Market.US: SOURCE, Market.TR: KAP_SOURCE}
KAP_CORRECTION_DAYS = 60  # a "Düzeltme" that names no index replaces the party's latest live filing this recent
UNIVERSE_DAYS = 400  # a 13F position change this recent keeps an issuer in the refresh universe
DEFAULT_DAYS = 90  # the window /stocks/{symbol}/insiders and the stock_detail block describe
MAX_TRANSACTIONS = 200  # per API payload, newest first; `truncated` says when older rows exist beyond it
FORM4_FORMS = ("4", "4/A")
OPEN_MARKET = {"P": "buy", "S": "sell"}  # the only codes that count as buyers / sellers in the summary
# The issuer's Form 4 list on EDGAR — where the rows beyond MAX_TRANSACTIONS live.
EDGAR_ISSUER_FORM4 = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={cik}&type=4&dateb=&owner=include&count=100"
# The issuer's own page on KAP (every disclosure it published, the insider filings among them), by its member oid.
KAP_ISSUER_PAGE = "https://www.kap.org.tr/tr/sirket-bilgileri/ozet/{oid}"


def build_client() -> EdgarClient:
    return EdgarClient(settings.sec_user_agent)


# --------------------------------------------------------------------------- refresh (write path)


def universe(session: Session, limit: int | None = None, *, mapped: bool | None = True) -> list[Instrument]:
    """US instruments worth asking EDGAR about: seen in a 13F position change within UNIVERSE_DAYS or on a
    watchlist — never the whole instrument table, most of which is CUSIP placeholders no ticker map knows (they have
    no ticker to look up and are left out here). `mapped` keeps the ones with a CIK (the default: what a run reads),
    `False` the ones the SEC ticker map does not know (ADR variants, preferreds, delisted names — reported as skipped,
    never given a slot of the daily cap), `None` both. Stalest first — never fetched, then the oldest
    `sec_form4_fetched_at`, then by symbol — and at most `limit` of them, so a capped daily run walks the whole
    universe over a few days instead of re-reading the same head (the fundamentals job orders itself the same way)."""
    since = date.today() - timedelta(days=UNIVERSE_DAYS)
    in_changes = select(PositionChange.instrument_id).where(PositionChange.period_end >= since)
    watched = select(WatchlistItem.instrument_id).where(WatchlistItem.instrument_id.is_not(None))
    stmt = (
        select(Instrument)
        .where(Instrument.market_code == Market.US, or_(Instrument.id.in_(in_changes), Instrument.id.in_(watched)),
               or_(Instrument.cusip.is_(None), Instrument.symbol != Instrument.cusip))
        .order_by(Instrument.sec_form4_fetched_at.asc().nulls_first(), Instrument.symbol)
    )
    if mapped is not None:
        stmt = stmt.where(Instrument.sec_cik.is_not(None) if mapped else Instrument.sec_cik.is_(None))
    if limit is not None:
        stmt = stmt.limit(limit)
    return session.scalars(stmt).all()


def refresh(session: Session, symbols: Sequence[str] | None = None, *, days_back: int | None = None, pause_s: float = 0.25,
            client: EdgarClient | None = None, as_of: date | None = None) -> dict[str, int]:
    """Store the last `days_back` days (default `sec_form4_days_back`) of filings of the stalest `sec_form4_max_issuers`
    issuers of `universe()` (or of the given `symbols`, uncapped). When a candidate lacks a CIK the SEC ticker map is
    read first (one request, committed on its own) and the batch is chosen after that, so an issuer the map does not
    know never occupies a slot: it is counted as skipped and picked up the day the map learns it. Per issuer (CIK —
    share classes are one issuer): the submissions listing → `sec_filings` of every class (idempotent), then every
    Form 4 / 4/A not stored yet is fetched and written, oldest first and a day's originals before its amendments so
    an amendment always finds its original; the issuer's work runs in a SAVEPOINT and is committed as soon as it is
    done, so an interrupted run keeps what it wrote and a failing issuer undoes its own writes alone — never the CIK
    sync, never the caller's uncommitted work. One issuer's failure is logged and skipped; an EDGAR refusal (403/429)
    or outage (5xx) ends the run early. `pause_s` sleeps before every request but the first — EDGAR's fair-use ceiling
    is 10 req/s and a burst of listings looks like a scrape. Returns counts: issuers (fetched), filings (new listing
    rows), form4 (new documents), transactions (new rows), skipped (no CIK, or failed)."""
    client = client or build_client()
    as_of = as_of or date.today()
    since = as_of - timedelta(days=days_back or settings.sec_form4_days_back)
    calls = 0

    def pace() -> None:
        nonlocal calls
        if calls and pause_s:
            time.sleep(pause_s)
        calls += 1

    if symbols:
        wanted = [s.strip().upper() for s in symbols if s.strip()]
        instruments = session.scalars(select(Instrument).where(Instrument.market_code == Market.US, Instrument.symbol.in_(wanted)).order_by(Instrument.symbol)).all()
        if missing := sorted(set(wanted) - {i.symbol for i in instruments}):
            log.warning("insiders: unknown US symbols skipped: %s", ", ".join(missing))
        if any(i.sec_cik is None for i in instruments):
            _map_ciks(session, client, pace)
        unmapped = [i for i in instruments if i.sec_cik is None]
        instruments = [i for i in instruments if i.sec_cik is not None]
    else:
        if universe(session, limit=1, mapped=False):
            _map_ciks(session, client, pace)
        unmapped = universe(session, mapped=False)
        instruments = universe(session, limit=settings.sec_form4_max_issuers)
    out = {"issuers": 0, "filings": 0, "form4": 0, "transactions": 0, "skipped": len(unmapped)}
    if unmapped:
        names = ", ".join(i.symbol for i in unmapped[:20]) + (" …" if len(unmapped) > 20 else "")
        log.info("insiders: %s instruments have no CIK in the SEC ticker map, skipped: %s", len(unmapped), names)

    done: set[str] = set()  # CIKs read in this run: the second share class of an issuer has nothing left to fetch
    for inst in instruments:
        if inst.sec_cik in done:
            continue
        try:
            with session.begin_nested():  # the issuer's savepoint: a failure below rolls back this issuer alone
                pace()
                listing = client.recent_filings(inst.sec_cik, limit=None, since=since.isoformat())
                classes = _issuer_instruments(session, inst.sec_cik)
                for cls in classes:
                    out["filings"] += _store_filings(session, cls, listing)
                for filing in sorted((f for f in listing if f["form"] in FORM4_FORMS), key=lambda f: (f["filed"], f["form"] == "4/A", f["accession"])):
                    if session.scalar(select(Disclosure.id).where(Disclosure.source == Source.SEC, Disclosure.source_id == filing["accession"])) is not None:
                        continue  # stored by an earlier run — a Form 4 never changes, corrections come as a 4/A
                    pace()
                    try:  # a document EDGAR cannot serve or the parser cannot read is logged and retried next run; the issuer goes on
                        doc = parse_form4(client.form4_document(inst.sec_cik, filing["accession"], filing["primary_document"]))
                    except (httpx.HTTPStatusError, ValueError, ElementTree.ParseError) as exc:
                        if isinstance(exc, httpx.HTTPStatusError) and (exc.response.status_code in (403, 429) or exc.response.status_code >= 500):
                            raise  # EDGAR itself is refusing: handled below, the run stops
                        log.warning("insiders: %s %s skipped: %s: %s", inst.symbol, filing["accession"], type(exc).__name__, str(exc)[:200])
                        continue
                    out["transactions"] += _store_form4(session, inst, filing, doc)
                    out["form4"] += 1
                now = datetime.now(UTC)
                for cls in classes:
                    cls.sec_form4_fetched_at = now
            done.add(inst.sec_cik)
            out["issuers"] += 1
            session.commit()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (403, 429) or status >= 500:
                log.warning("insiders: EDGAR answered %s for %s, stopping after %s issuers", status, inst.symbol, out["issuers"])
                break
            log.warning("insiders: %s skipped: HTTP %s", inst.symbol, status)
            out["skipped"] += 1
        except Exception as exc:  # noqa: BLE001 — one issuer never stops the batch
            log.warning("insiders: %s skipped: %s: %s", inst.symbol, type(exc).__name__, str(exc)[:200])
            out["skipped"] += 1
    return out


def _map_ciks(session: Session, client: EdgarClient, pace: Callable[[], None]) -> None:
    """The SEC ticker map → `Instrument.sec_cik` (one request), committed as its own unit of work before any issuer
    is read. A map that cannot be fetched is logged: the issuers that already carry a CIK still run."""
    try:
        with session.begin_nested():
            pace()
            tickers.refresh_ciks(session, client)
        session.commit()
    except Exception as exc:  # noqa: BLE001 — the map is a convenience
        log.warning("insiders: ticker map not refreshed: %s: %s", type(exc).__name__, str(exc)[:200])


def _issuer_instruments(session: Session, cik: str) -> list[Instrument]:
    """Every US instrument of the issuer: its share classes share the CIK (GOOG / GOOGL, LEN / LEN-B, BRK-A / BRK-B)."""
    return session.scalars(select(Instrument).where(Instrument.market_code == Market.US, Instrument.sec_cik == cik).order_by(Instrument.symbol)).all()


def _store_filings(session: Session, inst: Instrument, listing: list[dict]) -> int:
    """Upsert the issuer's listing rows keyed by (instrument, accession); a listing never changes once filed."""
    known = set(session.scalars(select(SecFiling.accession).where(SecFiling.instrument_id == inst.id)))
    n = 0
    for f in listing:
        if f["accession"] in known:
            continue
        session.add(SecFiling(
            instrument_id=inst.id, form=f["form"], filed_at=date.fromisoformat(f["filed"]), period=_date(f.get("period")),
            items=f.get("items") or None, accession=f["accession"], primary_document=f.get("primary_document"), url=f["url"],
        ))
        known.add(f["accession"])
        n += 1
    session.flush()
    return n


def _date(text: str | None) -> date | None:
    return date.fromisoformat(text[:10]) if text else None


def _decimal(text: str | None) -> Decimal | None:
    return Decimal(text) if text not in (None, "") else None


def _row_hash(accession: str, owner_cik: str, ordinal: int, t: dict) -> str:
    """Identity of one transaction row: the filing, the owner, the row's position in the document and its fields —
    two identical rows in one filing stay two rows, a re-run of the same document writes nothing."""
    key = "|".join(str(x) for x in (accession, owner_cik, ordinal, t["derivative"], t["date"], t["code"], t["shares"], t["price"], t["ownership_nature"]))
    return hashlib.sha256(key.encode()).hexdigest()


def _issuer_cik(doc: dict) -> str | None:
    return (doc.get("issuer") or {}).get("cik")


def _owner_ciks(doc: dict) -> set[str]:
    return {o["cik"] for o in doc.get("reporting_owners") or [] if o.get("cik")}


def _store_form4(session: Session, inst: Instrument, filing: dict, doc: dict) -> int:
    """One parsed Form 4 → its Disclosure (PARSED on arrival: nothing else needs to read it again) and its
    transaction rows. The rows belong to the issuer the document names: an issuer's listing also carries the Form 4s
    it files as a 10 % owner of another company (Occidental's on Western Midstream), and those rows go to that
    company's instrument when there is one, else nowhere. A joint filing (a director and their trust, a fund group)
    names several owners for one set of rows: the rows are attributed to the natural person when one is flagged
    director or officer, else the first owner, with the union of every owner's roles; the others stay in the
    payload and the cluster detector reads them from there. Returns the rows written."""
    now = datetime.now(UTC)
    accession = filing["accession"]
    payload_json = json.dumps(doc, sort_keys=True, default=str)
    disc = Disclosure(
        market_code=Market.US, source=Source.SEC, source_id=accession, kind=DisclosureKind.SEC_FORM4,
        published_at=datetime.fromisoformat(filing["filed"]), raw_uri=filing["url"],
        raw_hash=hashlib.sha256(payload_json.encode()).hexdigest(), payload=doc,
        parse_status=ParseStatus.PARSED, parsed_at=now,
    )
    session.add(disc)
    session.flush()
    issuer_cik = _issuer_cik(doc)
    target = inst
    if issuer_cik and issuer_cik != inst.sec_cik:
        target = session.scalar(select(Instrument).where(Instrument.market_code == Market.US, Instrument.sec_cik == issuer_cik).order_by(Instrument.symbol))
        if target is None:
            log.info("insiders: %s reports on issuer CIK %s (%s is a reporting owner) and no instrument carries it; document kept, no rows", accession, issuer_cik, inst.symbol)
            return 0
    owners = doc.get("reporting_owners") or []
    primary = primary_owner(owners)
    if primary is None:
        log.warning("insiders: %s names no reporting owner; document kept, no rows", accession)
        return 0
    roles = roles_of(*owners)
    title = officer_title(primary, owners)
    n = 0
    for ordinal, t in enumerate(doc.get("non_derivative_transactions", []) + doc.get("derivative_transactions", [])):
        if not t.get("date") or not t.get("code") or t.get("shares") is None:
            log.warning("insiders: %s row %s lacks date, code or shares; skipped", accession, ordinal)
            continue
        row_hash = _row_hash(accession, primary["cik"], ordinal, t)
        if session.scalar(select(InsiderTransaction.id).where(InsiderTransaction.row_hash == row_hash)) is not None:
            continue
        try:
            when, shares, price, post = date.fromisoformat(t["date"][:10]), _decimal(t["shares"]), _decimal(t.get("price")), _decimal(t.get("post_shares"))
        except (ValueError, InvalidOperation):  # a cell the filer typed by hand ("n/a", "12/31/2025"): the row is skipped, the document kept
            log.warning("insiders: %s row %s has an unreadable date or number; skipped", accession, ordinal)
            continue
        session.add(InsiderTransaction(
            disclosure_id=disc.id, instrument_id=target.id, insider_cik=primary["cik"], insider_name=(primary.get("name") or primary["cik"])[:160],
            roles=roles, title=title[:160] if title else None, transaction_date=when, filed_at=disc.published_at,
            code=t["code"][:2], acquired=(t.get("acquired_disposed") or "").upper() == "A", shares=shares, price=price,
            post_shares=post, ownership=(t.get("ownership_nature") or "D")[:1].upper(), derivative=bool(t["derivative"]),
            confidence=Confidence.EXACT, row_hash=row_hash,
        ))
        n += 1
    if doc.get("document_type") == "4/A":
        _supersede(session, disc, doc)
    else:
        _superseded_on_arrival(session, disc, doc)
    session.flush()
    return n


def _amendment_candidates(session: Session, disc: Disclosure, doc: dict, lower: datetime, upper: datetime) -> list[Disclosure]:
    """Live Form 4 documents of the same issuer and (overlapping) reporting owners published in [lower, upper],
    narrowed in SQL to the owners' rows or the issuer's filing folder before the payloads are loaded."""
    issuer_cik, owner_ciks = _issuer_cik(doc), _owner_ciks(doc)
    if not issuer_cik or not issuer_cik.isdigit() or not owner_ciks:
        return []
    stmt = (
        select(Disclosure).where(
            Disclosure.source == Source.SEC, Disclosure.kind == DisclosureKind.SEC_FORM4, Disclosure.is_superseded.is_(False),
            Disclosure.id != disc.id, Disclosure.published_at >= lower, Disclosure.published_at <= upper,
            or_(Disclosure.id.in_(select(InsiderTransaction.disclosure_id).where(InsiderTransaction.insider_cik.in_(owner_ciks))),
                Disclosure.raw_uri.like(f"%/edgar/data/{int(issuer_cik)}/%")),  # a holdings-only original has no rows: its filing folder finds it
        )
    )
    return [d for d in session.scalars(stmt) if _issuer_cik(d.payload) == issuer_cik and _owner_ciks(d.payload) & owner_ciks]


def _supersede(session: Session, disc: Disclosure, doc: dict) -> None:
    """A 4/A replaces the live Form 4 of the same owner for the same issuer: the one filed on
    `dateOfOriginalSubmission` when the amendment states it; failing that, the earlier amendment that names the same
    original (a second 4/A of one filing replaces the first, not the long-superseded original); failing that, the
    latest earlier document for the same period of report — filed within a week of the stated day when there is one
    (the filer put the transaction date in the original-date cell: the filing followed within two business days),
    any earlier one when none is stated. The original's rows are flagged, never deleted (the 13F-HR/A rule); when the
    original predates what was ever fetched there is nothing to supersede."""
    original_day = _date(doc.get("date_of_original_submission"))
    period = doc.get("period_of_report")
    lower = original_day if original_day else (_date(period) or disc.published_at.date()) - timedelta(days=60)
    candidates = _amendment_candidates(session, disc, doc, datetime.combine(lower, datetime.min.time()), disc.published_at)
    exact = [d for d in candidates if original_day and d.published_at.date() == original_day]
    prior = [d for d in candidates if original_day and d.payload.get("date_of_original_submission") == doc.get("date_of_original_submission")]
    same_period = [d for d in candidates if d.payload.get("period_of_report") == period and (not original_day or d.published_at.date() <= original_day + timedelta(days=7))]
    candidates = exact or prior or same_period
    if not candidates:
        return
    _mark_superseded(session, max(candidates, key=lambda d: (d.published_at, d.id)), disc)


def _superseded_on_arrival(session: Session, disc: Disclosure, doc: dict) -> None:
    """A plain Form 4 that arrives after its amendment — the original could not be read in the run that stored the
    4/A and is retried now — is flagged at once: the earliest live 4/A of the same owner and issuer that supersedes
    nothing yet and names this filing date as its original (or, stating none, reports the same period) replaces it."""
    later = [
        d for d in _amendment_candidates(session, disc, doc, disc.published_at, disc.published_at + timedelta(days=60))
        if d.payload.get("document_type") == "4/A" and d.supersedes_id is None
        and (d.payload.get("date_of_original_submission") == disc.published_at.date().isoformat()
             or (not d.payload.get("date_of_original_submission") and d.payload.get("period_of_report") == doc.get("period_of_report")))
    ]
    if later:
        _mark_superseded(session, disc, min(later, key=lambda d: (d.published_at, d.id)))


def _mark_superseded(session: Session, old: Disclosure, new: Disclosure) -> None:
    new.supersedes_id = old.id
    old.is_superseded = True
    for row in session.scalars(select(InsiderTransaction).where(InsiderTransaction.disclosure_id == old.id)):
        row.is_superseded = True
    session.flush()


# --------------------------------------------------------------------------- refresh (KAP, write path)


def build_kap_client() -> KapPublicAdapter:
    """The public-site adapter on its insider path: the PYŞ window, the insider job's own detail cap."""
    return KapPublicAdapter(days_back=settings.kap_public_days_back, max_details=settings.kap_insiders_max_details, pys_only=False)


def refresh_kap(session: Session, adapter: KapPublicAdapter | None = None, *, days_back: int | None = None) -> dict[str, int]:
    """Store the window's KAP person / shareholder filings not stored yet (`adapter.known` is every KAP disclosure
    index we hold, whichever path stored it). Each filing runs in a SAVEPOINT and is committed on its own, so an
    interrupted run keeps what it wrote and one bad page undoes nothing else. A page the parser cannot read is kept
    as a FAILED disclosure with the reason (no rows) and counted as skipped — it is visible in the disclosures table
    and never fetched again; a page the site cannot serve is skipped by the adapter and retried next run. Returns
    counts: listed (rows in the window), disclosures (new, stored PARSED), transactions (new rows), skipped."""
    adapter = adapter or build_kap_client()
    if days_back is not None:
        adapter.days_back = days_back
    adapter.known |= set(session.scalars(select(Disclosure.source_id).where(Disclosure.source == Source.KAP)))
    out = {"listed": 0, "disclosures": 0, "transactions": 0, "skipped": 0}
    for raw in adapter.iter_fetch_insiders(stats=out):
        try:
            with session.begin_nested():
                n = _store_kap_filing(session, raw)
            session.commit()
        except Exception as exc:  # noqa: BLE001 — one filing never stops the run
            log.warning("insiders: KAP %s skipped: %s: %s", raw.source_id, type(exc).__name__, str(exc)[:200])
            out["skipped"] += 1
            continue
        if n is None:
            out["skipped"] += 1
        else:
            out["disclosures"] += 1
            out["transactions"] += n
    return out


def _store_kap_filing(session: Session, raw: RawDisclosure) -> int | None:
    """One fetched page → its Disclosure and rows. Returns the rows written, or None when the page could not be
    normalised (the Disclosure is still stored, FAILED, with the reason). A re-run of a stored index never gets
    here (the adapter skips known indexes); a row's hash makes a replay of the same page a no-op anyway."""
    now = datetime.now(UTC)
    payload_json = json.dumps(raw.payload, sort_keys=True, default=str)
    disc = Disclosure(
        market_code=raw.market, source=raw.source, source_id=raw.source_id, kind=raw.kind, published_at=raw.published_at,
        raw_uri=raw.raw_uri, raw_hash=hashlib.sha256(payload_json.encode()).hexdigest(), payload=raw.payload,
        parse_status=ParseStatus.PENDING,
    )
    session.add(disc)
    session.flush()
    try:
        filing = parse_insider_filing(raw)
    except ValueError as exc:  # pydantic's ValidationError is one: the party, the numbers or the symbol are missing
        disc.parse_status, disc.parse_error = ParseStatus.FAILED, f"{type(exc).__name__}: {str(exc)[:900]}"
        log.warning("insiders: KAP %s (%s) kept without rows: %s", raw.source_id, raw.payload.get("summary"), str(exc).splitlines()[0][:160])
        return None
    inst = EntityResolver(session).instrument(Market.TR, filing.subject_symbol, filing.subject_name)
    n = 0
    for ordinal, r in enumerate(filing.rows):
        row_hash = _kap_row_hash(filing.source_id, filing.party_key, ordinal, r)
        if session.scalar(select(InsiderTransaction.id).where(InsiderTransaction.row_hash == row_hash)) is not None:
            continue
        session.add(InsiderTransaction(
            disclosure_id=disc.id, instrument_id=inst.id, insider_cik=filing.party_key, insider_name=filing.party_name[:160],
            roles=filing.roles, title=filing.title[:160] if filing.title else None, transaction_date=r.transaction_date, filed_at=disc.published_at,
            code=r.code, acquired=r.code == "P", shares=Decimal(r.nominal), price=r.price, post_shares=None, ownership="D", derivative=False,
            confidence=filing.confidence, row_hash=row_hash, kap_disclosure_index=int(filing.source_id), party_kind=filing.party_kind,
            post_pct_stake=r.post_pct_stake,
        ))
        n += 1
    disc.parse_status, disc.parsed_at = ParseStatus.PARSED, now
    if filing.is_correction:
        _supersede_kap(session, disc, filing, inst)
    else:
        _kap_superseded_on_arrival(session, disc, filing, inst)
    session.flush()
    return n


def _kap_row_hash(index: str, key: str, ordinal: int, r) -> str:
    """Identity of one KAP row: the disclosure index, the party, the row's position and its fields."""
    return hashlib.sha256("|".join(str(x) for x in (index, key, ordinal, r.transaction_date, r.code, r.nominal, r.price)).encode()).hexdigest()


def _kap_filings_of(session: Session, disc: Disclosure, filing: NormalizedInsiderFiling, inst: Instrument, lower: datetime, upper: datetime) -> list[Disclosure]:
    """Live KAP insider disclosures of the same party on the same stock published in [lower, upper] (the party is
    matched by key from the stored payload — KAP identifies it by name only)."""
    stmt = (
        select(Disclosure).where(
            Disclosure.source == Source.KAP, Disclosure.kind == DisclosureKind.KAP_INSIDER_TRANSACTION, Disclosure.is_superseded.is_(False),
            Disclosure.id != disc.id, Disclosure.published_at >= lower, Disclosure.published_at <= upper,
            Disclosure.id.in_(select(InsiderTransaction.disclosure_id).where(InsiderTransaction.instrument_id == inst.id)),
        )
    )
    return [d for d in session.scalars(stmt) if d.payload.get("party_name") and party_key(d.payload["party_name"]) == filing.party_key]


def _supersede_kap(session: Session, disc: Disclosure, filing: NormalizedInsiderFiling, inst: Instrument) -> None:
    """A "Düzeltme" replaces the disclosure it names (`amends_source_id`, from the page's related index); naming
    none, the party's latest live filing on the same stock within KAP_CORRECTION_DAYS. The old rows stay, flagged."""
    if filing.amends_source_id:
        old = session.scalar(select(Disclosure).where(Disclosure.source == Source.KAP, Disclosure.source_id == filing.amends_source_id, Disclosure.id != disc.id))
        if old is not None:
            _mark_superseded(session, old, disc)
        return
    candidates = _kap_filings_of(session, disc, filing, inst, disc.published_at - timedelta(days=KAP_CORRECTION_DAYS), disc.published_at)
    if candidates:
        _mark_superseded(session, max(candidates, key=lambda d: (d.published_at, d.id)), disc)


def _kap_superseded_on_arrival(session: Session, disc: Disclosure, filing: NormalizedInsiderFiling, inst: Instrument) -> None:
    """An original stored after its correction (the page could not be read in the run that stored the correction)
    is flagged at once by the earliest live correction of the same party and stock that names this index, or names
    none and supersedes nothing yet."""
    later = [
        d for d in _kap_filings_of(session, disc, filing, inst, disc.published_at, disc.published_at + timedelta(days=KAP_CORRECTION_DAYS))
        if d.payload.get("is_correction") and (d.payload.get("amends_source_id") == disc.source_id or (not d.payload.get("amends_source_id") and d.supersedes_id is None))
    ]
    if later:
        _mark_superseded(session, disc, min(later, key=lambda d: (d.published_at, d.id)))


# --------------------------------------------------------------------------- read models


def _issuer_ids(session: Session, instrument_id: int) -> list[int]:
    """The instrument and its share classes: one issuer's Form 4 rows are stored under whichever class was read first."""
    cik = session.scalar(select(Instrument.sec_cik).where(Instrument.id == instrument_id))
    if cik is None:
        return [instrument_id]
    return session.scalars(select(Instrument.id).where(Instrument.market_code == Market.US, Instrument.sec_cik == cik)).all()


def _rows(session: Session, instrument_id: int, start: date, as_of: date, *, limit: int | None = None, insiders_only: bool = False) -> list[tuple[InsiderTransaction, Disclosure]]:
    """Live (not superseded) rows of the issuer dated in (start, as_of], newest first, with their disclosure.
    `insiders_only` leaves out the company's own-share rows (`roles = "issuer"`): what the counts and the cluster read."""
    stmt = (
        select(InsiderTransaction, Disclosure).join(Disclosure, Disclosure.id == InsiderTransaction.disclosure_id)
        .where(InsiderTransaction.instrument_id.in_(_issuer_ids(session, instrument_id)), InsiderTransaction.is_superseded.is_(False),
               InsiderTransaction.transaction_date > start, InsiderTransaction.transaction_date <= as_of)
        .order_by(InsiderTransaction.transaction_date.desc(), InsiderTransaction.filed_at.desc(), InsiderTransaction.id)  # same day: document order
    )
    if insiders_only:
        stmt = stmt.where(InsiderTransaction.roles != ISSUER_ROLE)
    if limit is not None:
        stmt = stmt.limit(limit)
    return [(row, disc) for row, disc in session.execute(stmt)]


def trades(session: Session, instrument_id: int, start: date, as_of: date) -> list[InsiderTrade]:
    """The detector's view of the window's rows (never the company's own-share rows), each with every reporting
    owner of its filing (the filing's owners describe a row only when the row's insider is one of them)."""
    out = []
    for r, d in _rows(session, instrument_id, start, as_of, insiders_only=True):
        owners = _owner_ciks(d.payload)
        out.append(InsiderTrade(
            insider_cik=r.insider_cik, insider_name=r.insider_name, transaction_date=r.transaction_date, code=r.code, derivative=r.derivative,
            shares=r.shares, price=r.price, accession=d.source_id, owner_ciks=tuple(sorted(owners)) if r.insider_cik in owners else (r.insider_cik,),
            confidence=r.confidence,
        ))
    return out


def _value(row: InsiderTransaction) -> Decimal | None:
    return row.shares * row.price if row.price is not None else None


def _empty_summary() -> dict:
    return {"buyers": 0, "sellers": 0, "buy_value": 0.0, "sell_value": 0.0, "net_value": 0.0, "open_market_buys": 0, "open_market_sells": 0, "cluster": None}


def summary(session: Session, instrument_id: int, days: int = DEFAULT_DAYS, as_of: date | None = None) -> dict:
    """The contract's `summary` over the last `days`: buyers / sellers are distinct insiders with an open-market
    purchase (P) / sale (S) in the non-derivative table, the values sum shares × price of those rows (a row without
    a stated price adds nothing — a KAP filing that only states a range counts as unpriced), net = buy − sell, the
    counts are the number of such rows; `cluster` is the 30-day open-market purchase cluster (engine/insiders) or
    null. Grants, exercises, withholding and gifts, and the company's own-share rows on BIST, are listed in
    `transactions` but are not buys or sells here."""
    as_of = as_of or date.today()
    start = as_of - timedelta(days=days)
    rows = [r for r, _ in _rows(session, instrument_id, start, as_of, insiders_only=True) if not r.derivative and r.code in OPEN_MARKET]
    buys = [r for r in rows if r.code == "P"]
    sells = [r for r in rows if r.code == "S"]
    buy_value = sum((v for v in map(_value, buys) if v is not None), Decimal(0))
    sell_value = sum((v for v in map(_value, sells) if v is not None), Decimal(0))
    found = cluster(trades(session, instrument_id, as_of - timedelta(days=CLUSTER_WINDOW_DAYS), as_of), as_of)
    return {
        "buyers": len({r.insider_cik for r in buys}),
        "sellers": len({r.insider_cik for r in sells}),
        "buy_value": float(buy_value),
        "sell_value": float(sell_value),
        "net_value": float(buy_value - sell_value),
        "open_market_buys": len(buys),
        "open_market_sells": len(sells),
        "cluster": {"since": found["since"], "insiders": found["insiders"], "value": float(found["value"])} if found else None,
    }


def _price_range(row: InsiderTransaction, disc: Disclosure) -> list[float] | None:
    """[low, high] when a KAP filing states a price range and no single price (never a midpoint); null otherwise."""
    if row.kap_disclosure_index is None or row.price is not None:
        return None
    for r in disc.payload.get("rows") or []:
        if r.get("transaction_date") == row.transaction_date.isoformat() and r.get("side") == ("ALIS" if row.code == "P" else "SATIS") and r.get("price_low") and r.get("price_high"):
            return [float(r["price_low"]), float(r["price_high"])]
    return None


def _transaction_json(row: InsiderTransaction, disc: Disclosure) -> dict:
    """One row as the API and the tools report it — the same keys on both markets. US: `accession` and `url` are
    the EDGAR accession and filing index, `party_kind` / `post_pct_stake` / `price_range` null. TR: `accession` is
    the KAP disclosure index, `url` the disclosure page, `code` P (ALIŞ) or S (SATIŞ), `shares` the nominal (one
    lira = one share on BIST), `buyback` true for the company's own-share rows (labelled, never counted)."""
    value = _value(row)
    return {
        "id": row.id,
        "transaction_date": row.transaction_date.isoformat(),
        "filed_at": row.filed_at.isoformat(),
        "insider": row.insider_name,
        "insider_cik": row.insider_cik,
        "role": row.roles,
        "title": row.title,
        "code": row.code,
        "acquired": row.acquired,
        "shares": float(row.shares),
        "price": float(row.price) if row.price is not None else None,
        "price_range": _price_range(row, disc),
        "value": float(value) if value is not None else None,
        "post_shares": float(row.post_shares) if row.post_shares is not None else None,
        "post_pct_stake": float(row.post_pct_stake) if row.post_pct_stake is not None else None,
        "ownership": row.ownership,
        "derivative": row.derivative,
        "party_kind": row.party_kind,
        "buyback": row.roles == ISSUER_ROLE,
        "confidence": row.confidence,
        "accession": disc.source_id,
        "url": disc.raw_uri,
    }


def transactions(session: Session, instrument_id: int, days: int = DEFAULT_DAYS, limit: int = MAX_TRANSACTIONS, as_of: date | None = None) -> list[dict]:
    """The contract's `transactions`: every live row of the window (derivative rows flagged), newest first, at most `limit`."""
    as_of = as_of or date.today()
    return [_transaction_json(r, d) for r, d in _rows(session, instrument_id, as_of - timedelta(days=days), as_of, limit=limit)]


def _filing_json(f: SecFiling, cik: str | None) -> dict:
    primary = ARCHIVE.format(cik=int(cik), acc_nodash=f.accession.replace("-", "")) + f.primary_document if cik and f.primary_document else None
    return {"form": f.form, "filed_at": f.filed_at.isoformat(), "period": f.period.isoformat() if f.period else None, "items": f.items or [],
            "accession": f.accession, "url": f.url, "primary_url": primary}


def filings(session: Session, instrument_id: int, form: str | None = None, limit: int = 20) -> list[dict]:
    """The issuer's stored listing rows, newest first; `form` narrows to one form type."""
    inst = session.get(Instrument, instrument_id)
    stmt = select(SecFiling).where(SecFiling.instrument_id == instrument_id)
    if form:
        stmt = stmt.where(SecFiling.form == form)
    rows = session.scalars(stmt.order_by(SecFiling.filed_at.desc(), SecFiling.id.desc()).limit(limit))
    return [_filing_json(f, inst.sec_cik if inst else None) for f in rows]


def kap_fetched_at(session: Session) -> datetime | None:
    """When the KAP insider feed last stored a filing (any stock: the feed is read market-wide, not per issuer);
    None before the first run — the TR counterpart of `Instrument.sec_form4_fetched_at`."""
    return session.scalar(select(func.max(Disclosure.ingested_at)).where(Disclosure.kind == DisclosureKind.KAP_INSIDER_TRANSACTION))


def _fetched_at(session: Session, inst: Instrument) -> str | None:
    if inst.market_code == Market.TR:
        when = kap_fetched_at(session)
        return when.isoformat() if when else None
    return inst.sec_form4_fetched_at.isoformat() if inst.sec_form4_fetched_at else None


def kap_coverage_since(session: Session) -> date | None:
    """The earliest day the KAP insider feed has read (market-wide): the publication day of its oldest stored filing.
    The feed lists `kap_public_days_back` days per run, so the day after its first run a 90-day window is mostly
    unread — an empty window that starts before this day says nothing about the days before it. None before the
    first stored filing."""
    when = session.scalar(select(func.min(Disclosure.published_at)).where(Disclosure.kind == DisclosureKind.KAP_INSIDER_TRANSACTION))
    return when.date() if when else None


def _kap_issuer_url(session: Session, inst: Instrument) -> str | None:
    """The BIST issuer's page on KAP, known only from a filing it published itself (an issuer's ODA page carries its
    member oid; a relayed one carries KAP's own): the newest such stored filing on the stock, else None."""
    stmt = (
        select(Disclosure.payload).where(
            Disclosure.kind == DisclosureKind.KAP_INSIDER_TRANSACTION,
            Disclosure.id.in_(select(InsiderTransaction.disclosure_id).where(InsiderTransaction.instrument_id == inst.id)),
        ).order_by(Disclosure.published_at.desc(), Disclosure.id.desc()).limit(40)
    )
    for payload in session.scalars(stmt):
        if payload and not payload.get("relayed") and payload.get("member_code") == inst.symbol and payload.get("member_oid"):
            return KAP_ISSUER_PAGE.format(oid=payload["member_oid"])
    return None


def stock_insiders(session: Session, market: str, symbol: str, days: int = DEFAULT_DAYS, as_of: date | None = None) -> dict | None:
    """The /stocks/{symbol}/insiders payload, the same shape on both markets. None for an unknown symbol; a symbol
    nothing has been fetched for yet answers the empty shape with `supported: true` and `fetched_at: null` — never a
    placeholder number (US: the issuer has not been read; TR: the KAP feed has not run). `source` names the origin
    ("sec-edgar" / "kap"), `truncated` says the window holds more than the MAX_TRANSACTIONS rows returned;
    `more_url` is where they all are — the US issuer's Form 4 list on EDGAR (`edgar_url` too, the older name), the
    BIST issuer's page on KAP when one of its own filings told us its oid (every KAP row links its own disclosure
    page regardless). `coverage_since` (BIST only) is the earliest day the feed has read: an empty window is only
    "no activity" from that day on."""
    inst = session.scalar(select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol.upper()))
    if inst is None:
        return None
    as_of = as_of or date.today()
    base = {"symbol": inst.symbol, "name": inst.name, "market": inst.market_code, "days": days, "as_of": as_of.isoformat(), "source": SOURCE_OF.get(inst.market_code, SOURCE)}
    if inst.market_code not in SOURCE_OF:
        return {**base, "supported": False, "fetched_at": None, "coverage_since": None, "summary": _empty_summary(), "transactions": [], "truncated": False, "edgar_url": None, "more_url": None}
    rows = transactions(session, inst.id, days, limit=MAX_TRANSACTIONS + 1, as_of=as_of)
    edgar_url = EDGAR_ISSUER_FORM4.format(cik=int(inst.sec_cik)) if inst.market_code == Market.US and inst.sec_cik else None
    tr = inst.market_code == Market.TR
    coverage = kap_coverage_since(session) if tr else None
    return {
        **base, "supported": True,
        "fetched_at": _fetched_at(session, inst),
        "coverage_since": coverage.isoformat() if coverage else None,
        "summary": summary(session, inst.id, days, as_of),
        "transactions": rows[:MAX_TRANSACTIONS],
        "truncated": len(rows) > MAX_TRANSACTIONS,
        "edgar_url": edgar_url,
        "more_url": _kap_issuer_url(session, inst) if tr else edgar_url,
    }


def stock_filings(session: Session, market: str, symbol: str, form: str | None = None, limit: int = 20) -> dict | None:
    """The /stocks/{symbol}/filings payload; None for an unknown symbol, `supported: false` off the US market.
    `fetched_at` null with an empty list means the issuer has not been read yet — every listed US issuer files."""
    inst = session.scalar(select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol.upper()))
    if inst is None:
        return None
    if inst.market_code != Market.US:
        return {"symbol": inst.symbol, "market": inst.market_code, "supported": False, "fetched_at": None, "filings": []}
    return {"symbol": inst.symbol, "market": inst.market_code, "supported": True, "fetched_at": _fetched_at(session, inst), "filings": filings(session, inst.id, form, limit)}


def detail(session: Session, instrument: Instrument, as_of: date | None = None) -> dict | None:
    """The small `insiders` block of stock_detail: None until the source has been read once (the US issuer's Form 4
    listing, the KAP feed for BIST) — zeros would read as "no insider activity" when nothing was asked yet."""
    if instrument.market_code not in SOURCE_OF or _fetched_at(session, instrument) is None:
        return None
    s = summary(session, instrument.id, DEFAULT_DAYS, as_of)
    return {"days": DEFAULT_DAYS, "buyers": s["buyers"], "sellers": s["sellers"], "net_value": s["net_value"], "cluster": s["cluster"] is not None}


def last_filed_at(session: Session, market: str = Market.US) -> datetime | None:
    """When the market's newest stored insider row was filed (data freshness): Form 4 for US, KAP for TR."""
    return session.scalar(select(func.max(InsiderTransaction.filed_at)).join(Instrument, Instrument.id == InsiderTransaction.instrument_id).where(Instrument.market_code == market))


# --------------------------------------------------------------------------- signals (read by the pipeline)


def detected_signals(session: Session, as_of: date) -> list[tuple[Instrument, DetectedSignal]]:
    """INSIDER_BUY_CLUSTER for every instrument, on either market, with an open-market purchase (Form 4 code P, KAP
    ALIŞ) by someone other than the company itself in the cluster window ending on `as_of` — every share class of
    a US issuer, the rows are the issuer's. The pipeline writes the rows (one per episode, like every other signal)."""
    start = as_of - timedelta(days=CLUSTER_WINDOW_DAYS)
    ids = session.scalars(
        select(InsiderTransaction.instrument_id).where(
            InsiderTransaction.code == "P", InsiderTransaction.derivative.is_(False), InsiderTransaction.is_superseded.is_(False),
            InsiderTransaction.roles != ISSUER_ROLE, InsiderTransaction.transaction_date > start, InsiderTransaction.transaction_date <= as_of,
        ).distinct()
    ).all()
    out = []
    for instrument_id in sorted({i for stored in ids for i in _issuer_ids(session, stored)}):
        sig = detect_insider_buy_cluster(trades(session, instrument_id, start, as_of), as_of)
        if sig is not None:
            inst = session.get(Instrument, instrument_id)
            assert inst is not None
            out.append((inst, sig))
    return out
