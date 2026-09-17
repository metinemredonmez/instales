"""PROTOTYPE adapter over the public KAP website (kap.org.tr) — not for production.

MKK asks heavy consumers to use the licensed Veri Yayın Servisi; this adapter exists so we can
develop and validate against real disclosures while that contract is being signed. It is
deliberately polite: one list call per run, a hard cap on detail fetches, a delay between them,
and it never re-fetches a disclosure we already stored.

What it reads:
  POST /tr/api/disclosure/members/byCriteria  → list of disclosures for a date range
  GET  /tr/Bildirim/{index}                   → detail page; the disclosure is embedded as an RSC
                                                payload with `disclosureBasic` JSON + the content HTML
Scope: "Pay Alım Satım Bildirimi" filed by portfolio management companies (PYŞ), mapped onto the
canonical `KapShareTransactionPayload` (`iter_fetch`, the pipeline's ingest); and, on a separate path
(`iter_fetch_insiders`, services/insiders.refresh_kap), the same subject filed for a person or a shareholder —
the issuer's own filing about its director / executive / shareholder, or the one MKK relays on the person's
behalf under "KAMUYU AYDINLATMA PLATFORMU" with the SPK form attached as a PDF — mapped onto `KapInsiderPayload`.
"""

from __future__ import annotations

import html as html_mod
import json
import logging
import re
import time
from datetime import date, datetime, timedelta

import httpx
import pypdf.errors

from instilens.domain.enums import DisclosureKind, Market, Source
from instilens.domain.schemas import RawDisclosure
from instilens.ingestion.kap.pdr_pdf import merge_reports, parse_pdr_pdf, pdf_bytes

log = logging.getLogger("instilens.kap")
BASE = "https://www.kap.org.tr"
SUBJECT = "Pay Alım Satım Bildirimi"
REPORT_SUBJECT = "Portföy Dağılım Raporu"
DEFAULT_UA = "Mozilla/5.0 (compatible; InstiLens-prototype; +https://instilens.app)"
# The member a person's own SPK II-15.1 filing is published under: MKK relays it to KAP, the page only says who sent
# it ("… tarafından Kuruluşumuza gönderilen") and the filled-in form ("SÜREKLİ BİLGİLERE İLİŞKİN ÖZEL DURUM
# AÇIKLAMASI") is the PDF attachment. The issuer's own filings about its people use the ordinary ODA page.
KAP_MEMBER = "KAMUYU AYDINLATMA PLATFORMU"


class KapPublicAdapter:
    name = "kap-public"

    def __init__(
        self,
        user_agent: str = DEFAULT_UA,
        days_back: int = 7,
        max_details: int = 25,
        delay_seconds: float = 1.5,
        known_source_ids: set[str] | None = None,
        client: httpx.Client | None = None,
        pys_only: bool = True,
        max_reports: int = 15,
        fund_codes: list[str] | None = None,
    ) -> None:
        self.client = client or httpx.Client(
            base_url=BASE, timeout=httpx.Timeout(45.0, connect=15.0), follow_redirects=True,
            headers={"User-Agent": user_agent, "Accept-Language": "tr", "Content-Type": "application/json"},
        )
        self.days_back, self.max_details, self.delay = days_back, max_details, delay_seconds
        self.known = known_source_ids or set()
        self.pys_only = pys_only
        self.max_reports = max_reports
        self.fund_codes = {c.upper() for c in fund_codes} if fund_codes else None

    def _request(self, method: str, url: str, attempts: int = 4, **kw) -> httpx.Response:
        """Retry transient failures with backoff; KAP occasionally drops connections under load."""
        delay = 3.0
        for i in range(attempts):
            try:
                r = self.client.request(method, url, **kw)
                if r.status_code in (429, 502, 503, 504):
                    raise httpx.HTTPStatusError("retryable", request=r.request, response=r)
                return r.raise_for_status()
            except (httpx.TransportError, httpx.HTTPStatusError):
                if i == attempts - 1:
                    raise
                time.sleep(delay)
                delay *= 2

    # ------------------------------------------------------------------ list
    def _share_transaction_rows(self, from_date: date, to_date: date) -> list[dict]:
        """Every 'Pay Alım Satım Bildirimi' listing row of the range, whoever filed it (one request)."""
        body = {
            "fromDate": from_date.isoformat(), "toDate": to_date.isoformat(), "memberType": "IGS",
            "mkkMemberOidList": [], "inactiveMkkMemberOidList": [], "disclosureClass": "", "subjectList": [],
            "isLate": "", "mainSector": "", "sector": "", "subSector": "", "marketOid": "", "index": "",
            "bdkReview": "", "bdkMemberOidList": [], "year": "", "term": "", "ruleType": "", "period": "",
            "fromSrc": False, "srcCategory": "", "disclosureIndexList": [],
        }
        rows = self._request("POST", "/tr/api/disclosure/members/byCriteria", json=body).json()
        return [r for r in rows if r.get("subject") == SUBJECT]

    def list_disclosures(self, from_date: date, to_date: date) -> list[dict]:
        out = self._share_transaction_rows(from_date, to_date)
        if self.pys_only:
            out = [r for r in out if "PORTFÖY" in (r.get("kapTitle") or "").upper()]
        return sorted(out, key=lambda r: r["disclosureIndex"])

    def list_insider_disclosures(self, from_date: date, to_date: date) -> list[dict]:
        """The rows `list_disclosures` leaves out: the same subject filed by anyone but a PYŞ — the issuer about its
        own director / executive / shareholder (kapTitle = the issuer), a shareholder company, or MKK on a person's
        behalf (kapTitle KAP_MEMBER). Disjoint from the PYŞ list by construction, so a disclosure is never on both paths."""
        out = [r for r in self._share_transaction_rows(from_date, to_date) if "PORTFÖY" not in (r.get("kapTitle") or "").upper()]
        return sorted(out, key=lambda r: r["disclosureIndex"])

    def list_portfolio_reports(self, from_date: date, to_date: date) -> list[dict]:
        """Weekly 'Portföy Dağılım Raporu' filings by funds (equity-focused funds only unless a code list is set)."""
        body = {
            "fromDate": from_date.isoformat(), "toDate": to_date.isoformat(), "fundType": "", "fundOidList": [],
            "inactiveFundOidList": [], "mkkMemberOidList": [], "disclosureClass": "", "subjectList": [], "isLate": "",
            "year": "", "term": "", "ruleType": "", "period": "", "fromSrc": False, "srcCategory": "", "disclosureIndexList": [],
        }
        rows = self._request("POST", "/tr/api/disclosure/funds/byCriteria", json=body).json()
        out = [r for r in rows if r.get("subject") == REPORT_SUBJECT and r.get("fundCode")]
        if self.fund_codes:
            out = [r for r in out if r["fundCode"].upper() in self.fund_codes]
        else:
            out = [r for r in out if "HİSSE" in (r.get("kapTitle") or "").upper()]
        return sorted(out, key=lambda r: r["disclosureIndex"])

    def fetch_report(self, index: int, fund_code: str | None = None, fund_name: str | None = None) -> RawDisclosure | None:
        page = self.fetch_detail_html(index)
        full = decode_rsc(page)
        atts = [a for a in attachments_of(full) if a.get("fileExtension", "").lower() == "pdf"]
        if not atts:
            return None
        i = full.find('"disclosureBasic":')
        basic = _balanced_json(full, full.index("{", i)) if i >= 0 else {}
        published = datetime.strptime(basic["publishDate"], "%Y.%m.%d %H:%M:%S") if basic.get("publishDate") else datetime.now()
        parts = []
        for att in atts[:4]:
            time.sleep(self.delay)
            blob = self._request("GET", f"/tr/api/file/download/{att['objId']}").content
            try:
                parts.append(parse_pdr_pdf(blob, fund_code=fund_code, fund_name=fund_name, fallback_as_of=published.date()))
            except (ValueError, pypdf.errors.PyPdfError):  # scanned, or not a PDF at all (the site served an error page)
                continue
        if not parts:
            return None
        payload = merge_reports(parts)
        payload["member_oid"] = basic.get("mkkMemberOid")
        return RawDisclosure(
            market=Market.TR, source=Source.KAP, source_id=str(index), kind=DisclosureKind.KAP_PORTFOLIO_REPORT,
            published_at=published, raw_uri=f"{BASE}/tr/Bildirim/{index}", payload=payload,
        )

    # ------------------------------------------------------------------ detail
    def fetch_detail_html(self, index: int) -> str:
        return self._request("GET", f"/tr/Bildirim/{index}").text

    def fetch(self, since_source_id: str | None = None) -> list[RawDisclosure]:
        return list(self.iter_fetch(since_source_id))

    def iter_fetch(self, since_source_id: str | None = None):
        """Generator variant: the pipeline commits each disclosure as it arrives."""
        today = date.today()
        listing = self.list_disclosures(today - timedelta(days=self.days_back), today)
        details = 0
        for row in listing:
            idx = str(row["disclosureIndex"])
            if idx in self.known:
                continue
            if details >= self.max_details:
                break
            details += 1
            time.sleep(self.delay)
            try:
                raw = parse_detail_page(self.fetch_detail_html(int(idx)), fetch_attachment=self._download)
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, pypdf.errors.PyPdfError) as exc:  # one odd page must not end the run
                log.warning("kap detail %s skipped: %s", idx, exc)
                continue
            if raw is not None:
                yield raw
        reports = 0
        for row in self.list_portfolio_reports(today - timedelta(days=self.days_back), today):
            idx = str(row["disclosureIndex"])
            if idx in self.known:
                continue
            if reports >= self.max_reports:  # politeness cap counts ATTEMPTS (failed downloads cost the site too)
                break
            reports += 1
            time.sleep(self.delay)
            try:
                raw = self.fetch_report(int(idx), fund_code=row.get("fundCode"), fund_name=row.get("kapTitle"))
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, pypdf.errors.PyPdfError) as exc:  # one bad PDF must not stop the run; retried next time
                log.warning("kap report %s skipped: %s", idx, exc)
                continue
            if raw is not None:
                yield raw

    def fetch_insider(self, index: int) -> RawDisclosure | None:
        """One person / shareholder filing by index: the ODA page, plus the SPK form PDF when MKK relayed it."""
        return parse_insider_page(self.fetch_detail_html(index), fetch_attachment=self._download)

    def iter_fetch_insiders(self, stats: dict | None = None):
        """Generator over the window's insider filings not in `known`, under the same politeness as `iter_fetch`
        (one list call, `max_details` detail fetches counted as attempts, `delay` between them). `stats`, when
        given, receives `listed` (rows in the window) and `skipped` (pages that could not be read)."""
        today = date.today()
        listing = self.list_insider_disclosures(today - timedelta(days=self.days_back), today)
        if stats is not None:
            stats["listed"] = stats.get("listed", 0) + len(listing)
        details = 0
        for row in listing:
            idx = str(row["disclosureIndex"])
            if idx in self.known:
                continue
            if details >= self.max_details:
                break
            details += 1
            time.sleep(self.delay)
            try:
                raw = self.fetch_insider(int(idx))
            except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError, pypdf.errors.PyPdfError) as exc:  # one odd page must not end the run
                log.warning("kap insider %s skipped: %s", idx, exc)
                if stats is not None:
                    stats["skipped"] = stats.get("skipped", 0) + 1
                continue
            if raw is not None:
                yield raw

    def _download(self, obj_id: str) -> bytes:
        time.sleep(self.delay)
        return self._request("GET", f"/tr/api/file/download/{obj_id}").content


# ---------------------------------------------------------------------- parsing (pure functions)


def decode_rsc(page: str) -> str:
    """Join the Next.js RSC payload chunks (JSON string literals) into one decoded string."""
    chunks = re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', page)
    return "".join(json.loads('"' + c + '"') for c in chunks)


def _balanced_json(text: str, start: int) -> dict:
    depth = 0
    for k in range(start, len(text)):
        if text[k] == "{":
            depth += 1
        elif text[k] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start : k + 1])
    raise ValueError("unbalanced json")


def attachments_of(full: str) -> list[dict]:
    m = re.search(r'"attachments":(\[.*?\])(?=,"lang")', full)
    if not m:
        return []
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return []


def _cells(fragment: str) -> list[str]:
    cells = re.findall(r"<td[^>]*>(.*?)</td>", fragment, flags=re.S)
    return [html_mod.unescape(re.sub(r"<[^>]+>", "", c)).strip() for c in cells]


def _num(s: str) -> int:
    s = s.replace(".", "").replace(" ", "").split(",")[0]
    return int(s) if s.lstrip("-").isdigit() else 0


def _pct(s: str) -> str | None:
    m = re.search(r"(-?\d+(?:[.,]\d+)?)", s.replace("%", ""))
    return m.group(1).replace(",", ".") if m else None


def _bracket_list(text: str, label: str) -> list[str]:
    m = re.search(re.escape(label) + r".{0,200}?\[([^\]]*)\]", text, flags=re.S)
    return [c.strip().upper() for c in m.group(1).split(",") if c.strip()] if m else []


PROSE_TX = re.compile(
    r"(\d{2}\.\d{2}\.\d{4}) tarihinde .{0,120}?(\d{1,3}(?:\.\d{3})+|\d+) adet (alış|alım|satış|satım) işlemi",
    flags=re.I | re.S,
)
PROSE_RATIO = re.compile(r"%\s?(\d+[.,]\d+)'?[dt][ae]n\s*%\s?(\d+[.,]\d+)'?[ey]", flags=re.I)


def rows_from_prose(text: str) -> tuple[list[dict], str | None, str | None]:
    """Filings that put the numbers in prose (or only in the PDF): '11.09.2026 tarihinde … 3.027.970 adet satış işlemi'."""
    rows = []
    for d, n, kind in PROSE_TX.findall(text):
        rows.append({
            "transaction_date": datetime.strptime(d, "%d.%m.%Y").date().isoformat(),
            "side": "ALIS" if kind.lower().startswith("al") else "SATIS", "nominal": int(n.replace(".", "")),
        })
    ratios = PROSE_RATIO.findall(text)
    before = ratios[0][0].replace(",", ".") if ratios else None
    after = ratios[0][1].replace(",", ".") if ratios else None
    return rows, before, after


def parse_detail_page(page: str, fetch_attachment=None) -> RawDisclosure | None:
    full = decode_rsc(page)
    i = full.find('"disclosureBasic":')
    if i < 0:
        return None
    basic = _balanced_json(full, full.index("{", i))
    if basic.get("title") != SUBJECT:
        return None
    text = html_mod.unescape(re.sub(r"<[^>]+>", "|", full))
    companies = _bracket_list(text, "İlgili Şirketler")
    funds = _bracket_list(text, "İlgili Fonlar")
    if not companies:
        return None

    rows = []
    tbl = full.find("Transaction Date")
    if tbl > 0:
        seen = set()
        for cells in _table_rows(full[tbl : tbl + 40000]):
            key = tuple(cells)
            if key in seen or len(cells) < 4 or not re.match(r"\d{2}/\d{2}/\d{4}", cells[0]):
                continue
            seen.add(key)
            d = datetime.strptime(cells[0], "%d/%m/%Y").date()
            buy, sell = _num(cells[1]), _num(cells[2])
            before = _pct(cells[6]) if len(cells) > 6 else None
            after = _pct(cells[8]) if len(cells) > 8 else None
            if buy:
                rows.append({"transaction_date": d.isoformat(), "side": "ALIS", "nominal": buy, "before": before, "after": after})
            if sell:
                rows.append({"transaction_date": d.isoformat(), "side": "SATIS", "nominal": sell, "before": before, "after": after})
    source_note = "table"
    if not rows:
        prose_rows, pb, pa = rows_from_prose(text)
        if not prose_rows and fetch_attachment is not None:
            pdfs = [a for a in attachments_of(full) if a.get("fileExtension", "").lower() == "pdf"]
            if pdfs:
                att = pdfs[0]
                if True:
                    try:
                        import io

                        import pypdf

                        pdf_text = "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(io.BytesIO(pdf_bytes(fetch_attachment(att["objId"])))).pages)
                        prose_rows, pb, pa = rows_from_prose(pdf_text)
                        text = text + "|" + pdf_text
                    except Exception:
                        prose_rows = []
        if not prose_rows:
            try:  # last resort: Claude reads the prose/PDF text; every figure is validated against the text
                from instilens.ai.filing_extract import extract_transactions

                ai_rows, ai_funds, _ = extract_transactions(text.replace("|", "\n"))
            except Exception:
                ai_rows, ai_funds = [], []
            if ai_rows:
                rows = ai_rows
                funds = funds or ai_funds
                source_note = "ai"
            else:
                return None  # genuinely no numbers we can trust
        else:
            rows = [{**r, "before": pb, "after": pa} for r in prose_rows]
            source_note = "prose"

    avg_price = None
    m = re.search(r"ortalama fiyat[ıi]?\s*[:=]?\s*(\d+(?:[.,]\d+)?)", text, flags=re.I)
    if m:
        avg_price = m.group(1).replace(".", "").replace(",", ".") if m.group(1).count(",") == 1 and "." in m.group(1) else m.group(1).replace(",", ".")
    for r in rows:
        r["price"] = avg_price
    correction = is_correction_text(text)
    amends = related_disclosure_index(full, basic) if correction else None
    published = datetime.strptime(basic["publishDate"], "%Y.%m.%d %H:%M:%S")
    return RawDisclosure(
        market=Market.TR, source=Source.KAP, source_id=str(basic["disclosureIndex"]), kind=DisclosureKind.KAP_SHARE_TRANSACTION,
        published_at=published, raw_uri=f"{BASE}/tr/Bildirim/{basic['disclosureIndex']}",
        payload={
            "member_oid": basic["mkkMemberOid"], "member_name": basic["companyTitle"], "member_code": basic.get("stockCode"),
            "subject_symbol": companies[0], "related_companies": companies, "related_fund_codes": funds,
            "rows": [{k: v for k, v in r.items() if k in ("transaction_date", "side", "nominal", "price")} for r in rows],
            "ownership_before_pct": rows[0]["before"], "ownership_after_pct": rows[-1]["after"],
            "is_correction": correction, "amends_source_id": amends,
            "attachment_count": basic.get("attachmentCount", 0), "numbers_from": source_note,
        },
    )


def is_correction_text(text: str) -> bool:
    """'Yapılan Açıklama Düzeltme mi?' row of the ODA template. The tag-stripped text carries the TR and EN labels
    and both language values with many empty cells between them, so the answer is looked for in the segment up
    to the next template field (`oda_…` key or the next TR label), not in the adjacent cell."""
    m = re.search(r"Düzeltme mi\?(.{0,600}?)(?:oda_[A-Za-z]|Konuya İlişkin|Bildirim İçeriği|$)", text, flags=re.S)
    return bool(m and re.search(r"\bEvet\b", m.group(1)))


RELATED_INDEX_KEYS = ("relatedDisclosureIndex", "duzeltilenBildirimIndex", "correctedDisclosureIndex", "ilgiliBildirimIndex")
BILDIRIM_LINK = re.compile(r"/tr/Bildirim/(\d+)")
# Labels of the ODA correction / previous-notification fields. The link fallback below only looks inside these
# fields: a `/tr/Bildirim/<index>` taken from anywhere on the page would supersede an unrelated disclosure.
CORRECTION_FIELD_LABELS = ("oda_DateOfThePreviousNotification", "Daha Önce Yapılan Açıklama", "Düzeltilen Bildirim", "İlgili Bildirim")
CORRECTION_FIELD_WINDOW = 1500


def detail_blocks(full: str) -> list[dict]:
    """Every `disclosureDetail` object in the RSC payload, in page order. kap.org.tr emits two: a label dictionary
    near the top (`{"auditType": {"title": "Denetim Türü", …}, "opinionType": {…}}`) and, next to `disclosureBasic`,
    the filing's own detail — so the first block is never the one carrying `relatedDisclosureIndex`."""
    out: list[dict] = []
    for m in re.finditer(r'"disclosureDetail":', full):
        start = m.end()
        while start < len(full) and full[start] in " \t\r\n":
            start += 1
        if start >= len(full) or full[start] != "{":  # `"disclosureDetail":null` on filings without a detail block
            continue
        try:
            out.append(_balanced_json(full, start))
        except (ValueError, json.JSONDecodeError):
            continue
    return out


def related_disclosure_index(full: str, basic: dict) -> str | None:
    """Index of the disclosure a correction amends. kap.org.tr carries it as `relatedDisclosureIndex` in the RSC
    `disclosureDetail` block that sits beside `disclosureBasic` (null on ordinary filings); fall back to the same
    keys on `disclosureBasic` and, last, to a `/tr/Bildirim/<index>` link inside the correction field itself."""
    own = str(basic.get("disclosureIndex") or "")
    for source in (*detail_blocks(full), basic):
        for key in RELATED_INDEX_KEYS:
            value = source.get(key)
            if value not in (None, "", 0) and str(value).isdigit() and str(value) != own:
                return str(value)
    return _linked_index_in_correction_field(full, own)


def _linked_index_in_correction_field(full: str, own: str) -> str | None:
    """A `/tr/Bildirim/<index>` link inside the correction / previous-notification field, and only there: whatever
    this returns is marked superseded, so a link picked up from navigation or a "other disclosures" list would
    silently drop an unrelated disclosure's events from scoring. The field ends at the next ODA template key
    (`oda_…`), the same boundary `is_correction_text` uses."""
    for label in CORRECTION_FIELD_LABELS:
        for m in re.finditer(re.escape(label), full):
            segment = full[m.end() : m.end() + CORRECTION_FIELD_WINDOW]
            nxt = re.search(r"oda_[A-Za-z]", segment)
            for idx in BILDIRIM_LINK.findall(segment[: nxt.start()] if nxt else segment):
                if idx != own:
                    return idx
    return None


def _table_rows(fragment: str) -> list[list[str]]:
    return [_cells(tr) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", fragment, flags=re.S)]


# ---------------------------------------------------------------------- insider filings (persons / shareholders)

# The SPK II-15.1 form MKK relays as a PDF ("SÜREKLİ BİLGİLERE İLİŞKİN ÖZEL DURUM AÇIKLAMASI"): label → payload key.
# Read from pypdf's layout-mode text, where every field is one `Label : value` line.
FORM_FIELDS = {
    "Konu": "subject_line",
    "Yapılan Açıklama Düzeltme mi?": "correction_flag",
    "Bildirime Konu Borsa Şirketi": "subject_name",
    "Ad Soyad / Ticaret Ünvanı": "party_name",
    "Tüzel Kişi Adına Bildirimi Yapanın Adı Soyadı": "signatory",
    "Görevi": "role_text",
    "Varsa Birlikte Hareket Eden Diğer Gerçek-Tüzel Kişiler": "acting_with",
}
FORM_FIELD = {label: re.compile(r"^[ \t]*" + re.escape(label) + r"[ \t]*:[ \t]*(.*?)[ \t]*$", flags=re.M) for label in FORM_FIELDS}  # one line: an empty field stays empty
# A transaction-table line of the form (and of the free-form PDFs foreign holders attach): date, bought, sold, net,
# start-of-day and end-of-day nominal, then four ratios (capital / voting rights, start / end of day).
FORM_ROW = re.compile(r"^\s*(\d{2}[./]\d{2}[./]\d{4})\s+" + r"\s+".join([r"%?\s?(-?[\d.,]+)"] * 9) + r"\s*$", flags=re.M)
NUMBER = r"\d+(?:[.,]\d+)*"
# "103,2000 TL fiyatından", "28,80 TL fiyattan", "2,22 TL'den satılmıştır", "37,56 fiyattan": one stated price. The
# currency or the word "fiyat" is mandatory and a "%" before the number rules it out: "pay oranı %1,63'ten %2,03'e"
# is a stake, and a bare "10.000'den" a count — neither is ever read as a price.
PRICE_SINGLE = re.compile(
    r"(?<![\d.,%])(?<!%\s)(" + NUMBER + r")\s*(?:(?:TL|₺)\s*(?:fiyat(?:ından|tan|la|ıyla|ı ile)|'den|’den|'dan|’dan|'ten|’ten)|fiyat(?:ından|tan|la|ıyla|ı ile))",
    flags=re.I,
)
# "594 - 600 TL fiyat aralığından", "103,90 TL – 104,00 TL fiyat aralığından", "10.41 TL-10.69 TL", "2,69 ile 3,00 TL aralığında".
PRICE_RANGE = re.compile(r"(?<![\d.,])(" + NUMBER + r")\s*(?:TL|₺)?\s*(?:-|–|—|ile)\s*(" + NUMBER + r")\s*-?\s*(?:TL|₺)\s*(?:fiyat\s*)?aralığ", flags=re.I)
PRICE_AVERAGE = re.compile(r"ortalama(?:\s*fiyat[ıi]?)?\s*[:=]?\s*(" + NUMBER + r")", flags=re.I)
# Who acted, in the issuer's prose: "… Yönetim Kurulu Başkanı Sayın Süleyman Yıldırım tarafından", "… alış işlemi SMART
# HOLDİNG A.Ş. tarafından gerçekleştirilmiştir", "İzgi Holding A.Ş. tarafınca". The name is the run of capitalised
# tokens right before the word, walked backwards from it (`_name_before`).
BY_PARTY = re.compile(r"tarafın(?:dan|ca)\b")
# A "tarafından" that introduces a rule, a letter or a decision, not a trade: "Sermaye Piyasası Kurulu tarafından
# yayımlanan II-15.1", "MKK tarafından iletilen" — the words after it say so, and the party is looked for further on.
BY_PARTY_NOT_A_TRADE = re.compile(r"\s*(?:yayımlanan|yayınlanan|iletilen|gönderilen|düzenlenen|belirlenen|onaylanan|yapılan\s+(?:açıklama|düzenleme))", flags=re.I)
# Regulators and market institutions an issuer's prose cites; none of them is ever the acting party of a trade.
NOT_A_PARTY = re.compile(r"^(?:SPK|Sermaye Piyasası Kurulu(?:'?n?[ıu]n)?|MKK|Merkezi Kayıt (?:Kuruluşu|İstanbul)|Borsa İstanbul(?: A\.?Ş\.?)?|BİST|Takasbank|KAP|Kamuyu Aydınlatma Platformu)$", flags=re.I)
# A letter the issuer quotes: "Şirketimiz İmtiyazlı pay sahibi AG Girişim Holding A.Ş.'den gelen yazı".
LETTER_FROM = re.compile(r"(?:'|’)(?:den|dan|ndan|nden)\s+(?:gelen|alınan|iletilen)\s+(?:yazı|bildirim|açıklama)", flags=re.I)
# "… alış işlemi tarafımızca / Şirketimizce / ortaklığımızca gerçekleştirilmiştir": the disclosing company itself acted
# ("tarafımca", first person singular, is a quoted person and never the company).
BY_SELF = re.compile(r"\b(?:tarafım[ıi]zca|tarafımızdan|Şirketimizce|Şirketimiz tarafından|ortaklığımızca|kendi paylar)", flags=re.I)
# A shareholder writing through the issuer's page: "<issuer> ortağı Kristal Gıda … A.Ş. olarak kendi nam ve hesabıma".
AS_SHAREHOLDER = re.compile(r"ortağı\s+(.{3,160}?)\s+olarak\b", flags=re.S)
# Words that make a clause before a name a relationship ("Şirketimizin ana ortağı", "Yönetim Kurulu Başkanı Sayın");
# a clause without one is the transaction sentence, not a role.
ROLE_HINT = re.compile(r"ortağ|ortak|pay sahibi|hissedar|yönetim kurulu|genel müdür|başkan|üye|müdür|direktör|icra|\bceo\b|\bcfo\b|murahhas|kurucu|yönetici", flags=re.I)
# Tokens that end the backwards walk for a name and are not part of it — the honorifics, and the issuer's references
# to itself ("Şirketimiz tarafından" names nobody: BY_SELF reads it), all compared on the folded token.
NAME_STOP = {"sayin", "sn.", "sn", "tarafindan", "tarafinca", "oldugu", "ortagi", "sirketimiz", "sirketimizin", "ortakligimiz", "ortakligimizin", "sirket"}
NAME_JOINERS = {"ve", "and", "&", "of", "de", "da", "for"}
# A capitalised title word ends the walk too, so "Yönetim Kurulu Başkanı Ahmet Yılmaz" gives the person alone and the
# title lands in the role text — unless the word is a legal form ("… Yatırım Ortaklığı A.Ş."), which is part of a company's name.
TITLE_WORD = re.compile(r"(?:baskan|uye|mudur|ortag|ortak|sahib|hissedar|direktor|yardimci|yonetici|kurucu|koordinator)\w*$")
_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")
# The MKK page names the sender in one sentence: "… kapsamında Mehmet Kutman tarafından Kuruluşumuza gönderilen".
RELAYED_SENDER = re.compile(r"kapsamında\s+(.{2,160}?)\s+tarafından\s+Kuruluşumuza", flags=re.S)
RELATED_SYMBOL = re.compile(r"İlgili Şirketler\|(?:Related Companies\|)?\[?([A-Z0-9]{3,6})")


def parse_insider_page(page: str, fetch_attachment=None) -> RawDisclosure | None:
    """A 'Pay Alım Satım Bildirimi' page filed for a person or a shareholder → RawDisclosure (kind
    KAP_INSIDER_TRANSACTION) carrying a `KapInsiderPayload`-shaped payload. Two shapes: the issuer's own ODA page
    (the acting party and their role in the prose, the numbers in the standard table), and the page MKK publishes
    under KAP_MEMBER for a person's own filing (the sender in one sentence, everything else in the attached SPK
    form — read through `fetch_attachment(obj_id)` when given). None for a page that is not this subject; a page
    whose party or numbers cannot be read still comes back, with the gaps left None, so the caller can store it
    as a failed disclosure and never fetch it again — an attachment that is not a readable PDF (the site serving an
    error page under the objId) counts as such a gap, with `attachment_error` naming it, never as an exception.
    Nothing is estimated: a price range stays a range."""
    full = decode_rsc(page)
    i = full.find('"disclosureBasic":')
    if i < 0:
        return None
    basic = _balanced_json(full, full.index("{", i))
    if basic.get("title") != SUBJECT:
        return None
    text = html_mod.unescape(re.sub(r"<[^>]+>", "|", full))
    relayed = (basic.get("companyTitle") or "").strip().upper() == KAP_MEMBER
    member_code = (basic.get("stockCode") or "").strip().upper() or None
    companies = _bracket_list(text, "İlgili Şirketler")
    related = [c.strip().upper() for c in (basic.get("relatedStocks") or "").split(",") if c.strip()]
    m = RELATED_SYMBOL.search(text)
    subject = (companies[0] if companies else None) or (m.group(1) if m else None) or (related[0] if related else None) or member_code
    payload: dict = {
        "member_oid": basic.get("mkkMemberOid"), "member_name": basic.get("companyTitle"), "member_code": member_code,
        "subject_symbol": subject, "subject_name": None, "summary": basic.get("summary"), "relayed": relayed,
        "party_name": None, "party_kind": None, "party_is_issuer": False, "role_text": None, "signatory": None, "signatory_role": None,
        "acting_with": None, "rows": [], "post_pct_stake": None, "is_correction": False, "amends_source_id": None,
        "numbers_from": "table", "attachment_count": basic.get("attachmentCount", 0),
    }
    if relayed:
        sender = RELAYED_SENDER.search(text)
        payload["party_name"] = sender.group(1).strip() if sender else None
        pdfs = [a for a in attachments_of(full) if a.get("fileExtension", "").lower() == "pdf"]
        form: dict | None = None
        if pdfs and fetch_attachment is not None:
            try:
                form = parse_insider_form(pdf_text(fetch_attachment(pdfs[0]["objId"])))
            except pypdf.errors.PyPdfError as exc:
                log.warning("kap insider %s: attachment %s is not a readable PDF: %s", basic.get("disclosureIndex"), pdfs[0]["objId"], exc)
                payload["attachment_error"] = f"{type(exc).__name__}: {str(exc)[:200]}"
        if form is not None:
            for key in ("subject_name", "signatory", "role_text", "acting_with", "rows", "post_pct_stake", "prose"):
                if form.get(key) not in (None, "", []):
                    payload[key] = form[key]
            if form.get("party_name") and not payload["party_name"]:
                payload["party_name"] = form["party_name"]
            payload["form_party_name"] = form.get("party_name")
            payload["is_correction"] = bool(form.get("is_correction"))
            payload["numbers_from"] = "form"
    else:
        prose = explanation_text(text)
        payload["prose"] = prose
        payload["rows"] = table_transaction_rows(full)
        if not payload["rows"]:
            rows, _, after = rows_from_prose(prose)
            payload["rows"] = [{**r, "before": None, "after": after} for r in rows]
            payload["numbers_from"] = "prose"
        party, role, is_self = party_from_prose(prose)
        if is_self:
            party = basic.get("companyTitle")
            payload["party_is_issuer"] = subject == member_code
        payload["party_name"], payload["role_text"] = party, role
        payload["is_correction"] = is_correction_text(text)
        payload["post_pct_stake"] = payload["rows"][-1].get("after") if payload["rows"] else None
    _price_rows(payload)
    if payload["is_correction"]:
        payload["amends_source_id"] = related_disclosure_index(full, basic)
    if payload.get("party_name"):
        payload["party_kind"] = party_kind(payload["party_name"], signatory=payload.get("signatory"))
        if not payload["party_is_issuer"] and payload.get("subject_name"):
            payload["party_is_issuer"] = _same_name(payload["party_name"], payload["subject_name"])
        if not payload["party_is_issuer"] and not relayed and subject == member_code:
            payload["party_is_issuer"] = _same_name(payload["party_name"], basic.get("companyTitle") or "")
    if payload["party_kind"] == "company" and payload.get("role_text") and relayed:
        payload["signatory_role"], payload["role_text"] = payload["role_text"], None  # the form's "Görevi" is the signatory's job
    published = datetime.strptime(basic["publishDate"], "%Y.%m.%d %H:%M:%S")
    return RawDisclosure(
        market=Market.TR, source=Source.KAP, source_id=str(basic["disclosureIndex"]), kind=DisclosureKind.KAP_INSIDER_TRANSACTION,
        published_at=published, raw_uri=f"{BASE}/tr/Bildirim/{basic['disclosureIndex']}", payload=payload,
    )


def pdf_text(blob: bytes) -> str:
    """Layout-preserving text of a KAP PDF attachment (the form's `Label : value` lines survive only this way)."""
    import io

    import pypdf

    return "\n".join(p.extract_text(extraction_mode="layout") or "" for p in pypdf.PdfReader(io.BytesIO(pdf_bytes(blob))).pages)


def parse_insider_form(text: str) -> dict:
    """The SPK form's fields, transaction rows and prose. `rows` carry before/after ratios per day like the ODA
    table; `post_pct_stake` is the last day's end-of-day capital ratio. A PDF that is not the form (a foreign
    holder's own letter) still yields whatever table lines and prices it prints."""
    out: dict = {FORM_FIELDS[label]: None for label in FORM_FIELDS}
    for label, rx in FORM_FIELD.items():
        m = rx.search(text)
        if m and m.group(1).strip():
            out[FORM_FIELDS[label]] = m.group(1).strip()
    out["is_correction"] = (out.pop("correction_flag") or "").strip().upper().startswith("EVET")
    rows = []
    for m in FORM_ROW.finditer(text):
        buy, sell = _num(m.group(2)), _num(m.group(3))
        d = datetime.strptime(m.group(1).replace(".", "/"), "%d/%m/%Y").date()
        before, after = _pct(m.group(7)), _pct(m.group(9))
        if buy:
            rows.append({"transaction_date": d.isoformat(), "side": "ALIS", "nominal": buy, "before": before, "after": after})
        if sell:
            rows.append({"transaction_date": d.isoformat(), "side": "SATIS", "nominal": sell, "before": before, "after": after})
    out["rows"] = rows
    out["post_pct_stake"] = rows[-1]["after"] if rows else None
    head = text.find("Açıklama")
    tail = text.find("Ad Soyad")
    out["prose"] = re.sub(r"\s+", " ", text[head:tail] if 0 <= head < tail else text).strip()
    return out


def table_transaction_rows(full: str) -> list[dict]:
    """The standard 'Pay Alım Satım Bilgileri' table of an ODA page (TR and EN copies deduplicated): one ALIS
    and/or SATIS row per day with the day's start / end capital ratios. Rows with fewer than the ten standard
    cells (an issuer that dropped a column) are left out rather than misread."""
    rows: list[dict] = []
    tbl = full.find("Transaction Date")
    if tbl < 0:
        return rows
    seen = set()
    for cells in _table_rows(full[tbl : tbl + 40000]):
        key = tuple(cells)
        if key in seen or len(cells) < 10 or not re.match(r"\d{2}/\d{2}/\d{4}", cells[0]):
            continue
        seen.add(key)
        d = datetime.strptime(cells[0], "%d/%m/%Y").date()
        buy, sell = _num(cells[1]), _num(cells[2])
        before, after = _pct(cells[6]), _pct(cells[8])
        if buy:
            rows.append({"transaction_date": d.isoformat(), "side": "ALIS", "nominal": buy, "before": before, "after": after})
        if sell:
            rows.append({"transaction_date": d.isoformat(), "side": "SATIS", "nominal": sell, "before": before, "after": after})
    return [] if _two_party_table(rows) else rows


def _two_party_table(rows: list[dict]) -> bool:
    """An issuer that prints the counterparty's holdings as a second block (its treasury shares sold to a director's
    company: one row 0 / 1.750.000 at 1,45 % → 1,36 %, another 1.750.000 / 0 at 0,26 % → 0,35 %) cannot be read
    without guessing who owns which row: the same day's purchase and sale of one nominal at different ratios."""
    by_day: dict[str, list[dict]] = {}
    for r in rows:
        by_day.setdefault(r["transaction_date"], []).append(r)
    for same in by_day.values():
        buys = [r for r in same if r["side"] == "ALIS"]
        sells = [r for r in same if r["side"] == "SATIS"]
        if any(b["nominal"] == s["nominal"] and b["after"] != s["after"] for b in buys for s in sells):
            return True
    return False


def explanation_text(text: str) -> str:
    """The filer's prose of an ODA page: from the explanation block to the transaction table."""
    start = text.find("oda_ExplanationTextBlock")
    end = text.find("Pay Alım Satım Bilgileri|Shares Transaction Information|İşlem Tarihi", start if start >= 0 else 0)
    chunk = text[start + len("oda_ExplanationTextBlock") : end if end > start else None] if start >= 0 else text
    return re.sub(r"\s*\|\s*", " | ", chunk).strip(" |")


def _fold(text: str) -> str:
    """Lower-case ASCII of a Turkish token ("BAŞKANI" → "baskani"): the dotted capitals go first, because Python's
    lower() turns "İ" into "i" + a combining dot that no pattern would match."""
    return text.replace("İ", "i").replace("I", "ı").lower().translate(_FOLD)


def _name_before(chunk: str) -> tuple[str, str]:
    """(name, lead) — the capitalised tokens that end `chunk`, and the text before them. "Şirketimizin Yönetim
    Kurulu Başkanı Sayın Süleyman Yıldırım" → ("Süleyman Yıldırım", "Şirketimizin Yönetim Kurulu Başkanı Sayın"),
    and without the honorific "Yönetim Kurulu Başkanı Ahmet Yılmaz" → ("Ahmet Yılmaz", "Yönetim Kurulu Başkanı"):
    a title word ends the walk like a stop word does, a legal-form word never does."""
    tokens = chunk.split()
    name: list[str] = []
    while tokens:
        tok = tokens[-1]
        bare = re.sub(r"(?:'|’)[a-zçğıöşü]+$", "", tok)  # drop a possessive suffix: "Yıldırım'ın" → "Yıldırım"
        folded = _fold(bare)
        if folded in NAME_STOP or not bare:
            break
        if TITLE_WORD.search(folded) and not LEGAL_FORM.search(f" {bare.upper()} "):
            break
        if bare[0].isupper() or (name and folded in NAME_JOINERS):
            name.insert(0, bare)
            tokens.pop()
            continue
        break
    while name and name[0].lower() in NAME_JOINERS:
        name.pop(0)
    return " ".join(name).strip(" ,;:"), " ".join(tokens)


def party_from_prose(prose: str) -> tuple[str | None, str | None, bool]:
    """(party name, role text, acted itself). A quoted letter names its author; else the run of capitalised
    tokens before a "tarafından" / "tarafınca" that introduces a trade is the party and the words before that (up
    to the sentence start) the role text ("Şirketimizin ana ortağı", "Yönetim Kurulu Başkanı") — a regulator's
    "tarafından yayımlanan" and the issuer's "Şirketimiz tarafından" name no party; else "tarafımızca" /
    "Şirketimizce" / "Şirketimiz tarafından" means the discloser itself. The role text is mapped in parsing/kap_insider."""
    letter = LETTER_FROM.search(prose)
    if letter:
        name, lead = _name_before(prose[max(0, letter.start() - 160) : letter.start()])
        if name:
            return name, _role_lead(lead), False
    for m in BY_PARTY.finditer(prose):
        if BY_PARTY_NOT_A_TRADE.match(prose, m.end()):
            continue
        chunk = prose[max(0, m.start() - 200) : m.start()]
        chunk = chunk.split(" | ")[-1]  # never cross a paragraph
        name, lead = _name_before(chunk)
        if name and NOT_A_PARTY.match(name):
            continue
        if name and not re.search(r"\b(?:TL|adet|nominal)\b", name):  # a number's unit is not a name
            return name, _role_lead(lead), False
    holder = AS_SHAREHOLDER.search(prose)
    if holder:
        name, _ = _name_before(holder.group(1))
        if name:
            return name, "ortağı", False
    if BY_SELF.search(prose):
        return None, None, True
    return None, None, False


def _role_lead(lead: str) -> str | None:
    """The relationship words right before a name: the last clause of `lead` (split at sentence punctuation, never
    inside a number, and after a cited rule — "SPK tarafından yayımlanan II-15.1 kapsamında Yönetim Kurulu Üyesi"
    keeps only the title) without the issuer's self-reference — and only when it names a relationship at all."""
    tail = re.split(r"[.;:]\s|\s\|\s|\btarafın(?:dan|ca)\s|\b(?:kapsamında|uyarınca|gereğince|çerçevesinde|doğrultusunda|göre)\s", lead)[-1]
    tail = re.sub(r"\b(?:Şirketimiz(?:in)?|Ortaklığımız(?:ın)?|Sayın|Sn\.?)(?=\s|$)", " ", tail)  # "Sn." goes with its dot
    tail = re.sub(r"\s+", " ", tail).strip(" ,.;")
    return tail if tail and ROLE_HINT.search(tail) else None


def _price(token: str) -> str | None:
    """A price as printed: '103,2000' → '103.2000', '1.234,56' → '1234.56', '10.41' → '10.41' (a dot with two
    decimals is a decimal point, a dot with three digits after it a thousands separator)."""
    t = token.strip()
    if "," in t:
        return t.replace(".", "").replace(",", ".")
    if re.fullmatch(r"\d{1,3}(?:\.\d{3})+", t):
        return t.replace(".", "")
    return t if re.fullmatch(r"\d+(?:\.\d+)?", t) else None


def prices_in(sentence: str) -> tuple[str | None, str | None, str | None]:
    """(price, low, high) stated in one sentence: the single price or the average when there is one, the range
    otherwise — never a midpoint."""
    avg = PRICE_AVERAGE.search(sentence)
    rng = PRICE_RANGE.search(sentence)
    single = PRICE_SINGLE.search(sentence)
    low, high = (_price(rng.group(1)), _price(rng.group(2))) if rng else (None, None)
    price = _price(avg.group(1)) if avg else (_price(single.group(1)) if single and not rng else None)
    if price is None and low is not None and low == high:  # "16,46 - 16,46 TL fiyat aralığından": one price
        price, low, high = low, None, None
    return price, low, high


def _price_rows(payload: dict) -> None:
    """Attach the stated price (or range) to each row: from the sentence that names the row's date when the prose
    has one per day, else from the whole prose when the filing describes one day."""
    prose = payload.get("prose") or ""
    days = {r["transaction_date"] for r in payload["rows"]}
    for r in payload["rows"]:
        d = date.fromisoformat(r["transaction_date"])
        stamps = (d.strftime("%d.%m.%Y"), d.strftime("%d/%m/%Y"))
        sentences = [s for s in re.split(r"(?<=[.!?])\s+(?=[A-ZÇĞİÖŞÜ\d\"“])|\s\|\s", prose) if any(st in s for st in stamps)]
        source = " ".join(sentences) if sentences else (prose if len(days) == 1 else "")
        r["price"], r["price_low"], r["price_high"] = prices_in(source)
        r.pop("before", None)
        r["post_pct_stake"] = r.pop("after", None)


LEGAL_FORM = re.compile(r"(?:^|[\s,(.])(?:A\.?Ş\.?|A\.S\.?|T\.A\.Ş\.?|LTD\.?|ŞTİ\.?|STI\.?|HOLDİNG|HOLDING|INC\.?|CORP\.?|CORPORATION|LLC|GMBH|AG|SE|KG|S\.?A\.?|S\.?P\.?A\.?|S\.?A\.?R\.?L\.?|S\.?A\.?S\.?|B\.?V\.?|N\.?V\.?|PLC|LIMITED|LTD|LLP|L\.?P\.?|PTE|PTY|OYJ?|AB|KULÜBÜ|VAKFI|DERNEĞİ|BANK|BANKASI|KOOPERATİFİ|ORTAKLIĞI|ŞİRKETİ|COMPANY|CO\.|SANAYİ|TİCARET|YATIRIM)(?=$|[\s,.)'’])", flags=re.I)
FUND_NAME = re.compile(r"(?:^|\s)(?:FONU?|FUND|ETF|YATIRIM ORTAKLIĞI)(?=$|[\s,.)'’])", flags=re.I)


def party_kind(name: str, signatory: str | None = None) -> str:
    """person | company | fund | other from the name as filed: a fund word, a legal-form word (or a signatory,
    which only a legal entity has), else a natural person's two to five words."""
    upper = name.upper()
    if FUND_NAME.search(upper):
        return "fund"
    if signatory or LEGAL_FORM.search(upper):
        return "company"
    words = [w for w in re.split(r"\s+", re.sub(r"\([^)]*\)", " ", name).strip()) if w]  # "Levent Sadık Amet (Sadık Ahmet)": an alias
    if 2 <= len(words) <= 5 and all(re.fullmatch(r"[A-Za-zÇĞİÖŞÜçğıöşü.'’-]+", w) for w in words):
        return "person"
    return "other"


def _same_name(a: str, b: str) -> bool:
    """The same entity however the legal form is printed: "ATLANTİS YATIRIM HOLDİNG" and "ATLANTİS YATIRIM HOLDİNG A.Ş."
    are one issuer (the prose drops the suffix, the page header keeps it), so the LEGAL_FORM words go before the
    folded, letters-only comparison — and a name that was nothing but legal forms matches nothing."""
    key = lambda s: re.sub(r"[^a-z0-9]", "", _fold(LEGAL_FORM.sub(" ", s.upper())))  # noqa: E731
    ka, kb = key(a or ""), key(b or "")
    return bool(ka and kb) and ka == kb
