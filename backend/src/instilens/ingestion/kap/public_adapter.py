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
canonical `KapShareTransactionPayload`.
"""

from __future__ import annotations

import html as html_mod
import json
import re
import time
from datetime import date, datetime, timedelta

import httpx

from instilens.domain.enums import DisclosureKind, Market, Source
from instilens.domain.schemas import RawDisclosure
from instilens.ingestion.kap.pdr_pdf import merge_reports, parse_pdr_pdf, pdf_bytes

BASE = "https://www.kap.org.tr"
SUBJECT = "Pay Alım Satım Bildirimi"
REPORT_SUBJECT = "Portföy Dağılım Raporu"
DEFAULT_UA = "Mozilla/5.0 (compatible; InstiLens-prototype; +https://instilens.app)"


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
    def list_disclosures(self, from_date: date, to_date: date) -> list[dict]:
        body = {
            "fromDate": from_date.isoformat(), "toDate": to_date.isoformat(), "memberType": "IGS",
            "mkkMemberOidList": [], "inactiveMkkMemberOidList": [], "disclosureClass": "", "subjectList": [],
            "isLate": "", "mainSector": "", "sector": "", "subSector": "", "marketOid": "", "index": "",
            "bdkReview": "", "bdkMemberOidList": [], "year": "", "term": "", "ruleType": "", "period": "",
            "fromSrc": False, "srcCategory": "", "disclosureIndexList": [],
        }
        rows = self._request("POST", "/tr/api/disclosure/members/byCriteria", json=body).json()
        out = [r for r in rows if r.get("subject") == SUBJECT]
        if self.pys_only:
            out = [r for r in out if "PORTFÖY" in (r.get("kapTitle") or "").upper()]
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
            except ValueError:
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
            except httpx.HTTPError:
                continue  # retried already; leave it for the next run
            if raw is not None:
                yield raw
        reports = 0
        for row in self.list_portfolio_reports(today - timedelta(days=self.days_back), today):
            idx = str(row["disclosureIndex"])
            if idx in self.known or reports >= self.max_reports:
                continue
            time.sleep(self.delay)
            try:
                raw = self.fetch_report(int(idx), fund_code=row.get("fundCode"), fund_name=row.get("kapTitle"))
            except (httpx.HTTPError, ValueError, KeyError):  # one bad PDF must not stop the run; retried next time
                continue
            if raw is not None:
                yield raw
                reports += 1

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
    correction = bool(re.search(r"Düzeltme mi\?\|[^|]*\|Evet", text))
    published = datetime.strptime(basic["publishDate"], "%Y.%m.%d %H:%M:%S")
    return RawDisclosure(
        market=Market.TR, source=Source.KAP, source_id=str(basic["disclosureIndex"]), kind=DisclosureKind.KAP_SHARE_TRANSACTION,
        published_at=published, raw_uri=f"{BASE}/tr/Bildirim/{basic['disclosureIndex']}",
        payload={
            "member_oid": basic["mkkMemberOid"], "member_name": basic["companyTitle"], "member_code": basic.get("stockCode"),
            "subject_symbol": companies[0], "related_companies": companies, "related_fund_codes": funds,
            "rows": [{k: v for k, v in r.items() if k in ("transaction_date", "side", "nominal", "price")} for r in rows],
            "ownership_before_pct": rows[0]["before"], "ownership_after_pct": rows[-1]["after"],
            "is_correction": correction, "attachment_count": basic.get("attachmentCount", 0), "numbers_from": source_note,
        },
    )


def _table_rows(fragment: str) -> list[list[str]]:
    return [_cells(tr) for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", fragment, flags=re.S)]
