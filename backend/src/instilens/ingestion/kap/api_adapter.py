"""Official KAP Veri Yayın Servisi (VYK) REST adapter — MKK API Portal product "KAP Data Dissemination Services".

Spec (OpenAPI, apiportal.mkk.com.tr): Basic auth (API key / secret), base https://apigwdev.mkk.com.tr/api/vyk
  GET /lastDisclosureIndex                                  → {"lastDisclosureIndex"}
  GET /disclosures?disclosureIndex=N[&disclosureClass=ODA]  → first 50 disclosures from N: index, type, class, title, companyId, fundId, fundCode
  GET /disclosureDetail/{index}?fileType=html|data          → sender, behalfFund*, relatedStocks, subject, summary, time, attachmentUrls, content
  GET /downloadAttachment/{id}                              → binary (id = attachmentUrls[].url)
Free plan throttles at 6 calls/min → a token bucket paces every call (`min_interval`). Progress is
by disclosure index, so runs resume exactly where they stopped.
"""

from __future__ import annotations

import html as html_mod
import re
import time
from collections.abc import Callable, Iterator
from datetime import datetime

import httpx

from instilens.domain.enums import DisclosureKind, Market, Source
from instilens.domain.schemas import RawDisclosure
from instilens.ingestion.kap.pdr_pdf import merge_reports, parse_pdr_pdf
from instilens.ingestion.kap.public_adapter import _table_rows, rows_from_prose

TX_TITLE = "Pay Alım Satım Bildirimi"
REPORT_TITLE = "Portföy Dağılım Raporu"


class KapApiAdapter:
    name = "kap-api"

    def __init__(
        self,
        base_url: str,
        api_key: str,
        api_secret: str,
        rate_per_min: int = 6,
        max_calls: int = 120,
        pys_only: bool = True,
        start_index: int | None = None,
        known_source_ids: set[str] | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        self.client = client or httpx.Client(base_url=base_url.rstrip("/"), auth=(api_key, api_secret), timeout=45, headers={"Accept": "application/json"})
        self.min_interval = 60.0 / max(rate_per_min, 1)
        self.max_calls, self.pys_only, self.start_index = max_calls, pys_only, start_index
        self.known = known_source_ids or set()
        self._last_call = 0.0
        self._calls = 0

    # ------------------------------------------------------------------ transport
    def _get(self, path: str, **params) -> httpx.Response:
        wait = self.min_interval - (time.monotonic() - self._last_call)
        if wait > 0:
            time.sleep(wait)
        for attempt in range(3):
            r = self.client.get(path, params={k: v for k, v in params.items() if v is not None})
            self._last_call = time.monotonic()
            self._calls += 1
            if r.status_code == 429:
                time.sleep(self.min_interval * (attempt + 2))
                continue
            r.raise_for_status()
            return r
        r.raise_for_status()
        return r

    def last_index(self) -> int:
        return int(self._get("/lastDisclosureIndex").json()["lastDisclosureIndex"])

    def list_from(self, index: int) -> list[dict]:
        data = self._get("/disclosures", disclosureIndex=str(index)).json()
        return data if isinstance(data, list) else data.get("items") or data.get("data") or [data]

    def detail(self, index: int, file_type: str = "html") -> dict:
        return self._get(f"/disclosureDetail/{index}", fileType=file_type).json()

    def attachment(self, url_or_id: str) -> bytes:
        ident = url_or_id.rsplit("/", 1)[-1]
        return self._get(f"/downloadAttachment/{ident}").content

    # ------------------------------------------------------------------ fetch
    def fetch(self, since_source_id: str | None = None) -> list[RawDisclosure]:
        return list(self.iter_fetch(since_source_id))

    def iter_fetch(self, since_source_id: str | None = None) -> Iterator[RawDisclosure]:
        last = self.last_index()
        cursor = self.start_index or (int(since_source_id) + 1 if since_source_id and since_source_id.isdigit() else max(last - 500, 1))
        while cursor <= last and self._calls < self.max_calls:
            page = self.list_from(cursor)
            if not page:
                break
            for row in page:
                idx = int(row.get("disclosureIndex"))
                cursor = max(cursor, idx + 1)
                if str(idx) in self.known or self._calls >= self.max_calls:
                    continue
                title = (row.get("title") or "").strip()
                if title == TX_TITLE:
                    raw = self._transaction(idx, row)
                elif title == REPORT_TITLE and row.get("fundCode"):
                    raw = self._report(idx, row)
                else:
                    continue
                if raw is not None:
                    yield raw

    # ------------------------------------------------------------------ mapping
    def _transaction(self, idx: int, row: dict) -> RawDisclosure | None:
        d = self.detail(idx, "html")
        sender = d.get("senderTitle") or d.get("behalfSenderTitle") or ""
        if self.pys_only and "PORTFÖY" not in sender.upper():
            return None
        html = _html_of(d)
        text = html_mod.unescape(re.sub(r"<[^>]+>", "|", html))
        companies = _bracket_list(text, "İlgili Şirketler") or [s.get("code") if isinstance(s, dict) else str(s) for s in (d.get("relatedStocks") or [])][:1]
        funds = _bracket_list(text, "İlgili Fonlar")
        if not funds and d.get("behalfFundCode"):
            funds = [d["behalfFundCode"]]
        if not companies:
            return None
        rows, before, after = _rows_from_html(html)
        numbers_from = "table"
        if not rows:
            rows, before, after = rows_from_prose(text)
            numbers_from = "prose"
        if not rows:
            return None
        published = _time(d.get("time"))
        return RawDisclosure(
            market=Market.TR, source=Source.KAP, source_id=str(idx), kind=DisclosureKind.KAP_SHARE_TRANSACTION,
            published_at=published, raw_uri=d.get("link") or f"https://www.kap.org.tr/tr/Bildirim/{idx}",
            payload={
                "member_oid": str(d.get("senderId") or d.get("behalfSenderId") or ""), "member_name": sender,
                "subject_symbol": companies[0].upper(), "related_companies": [c.upper() for c in companies],
                "related_fund_codes": [f.upper() for f in funds], "rows": rows,
                "ownership_before_pct": before, "ownership_after_pct": after,
                "amends_source_id": str(d["relatedDisclosureIndex"]) if d.get("relatedDisclosureIndex") else None,
                "numbers_from": numbers_from, "attachment_count": len(d.get("attachmentUrls") or []),
            },
        )

    def _report(self, idx: int, row: dict) -> RawDisclosure | None:
        d = self.detail(idx, "html")
        atts = [a for a in (d.get("attachmentUrls") or []) if (a.get("fileName") or "").lower().endswith(".pdf")]
        if not atts:
            return None
        published = _time(d.get("time"))
        parts = []
        for a in atts[:3]:
            try:
                parts.append(parse_pdr_pdf(self.attachment(a["url"]), fund_code=row.get("fundCode"), fund_name=d.get("behalfFundTitle"), fallback_as_of=published.date()))
            except ValueError:
                continue
        if not parts:
            return None
        payload = merge_reports(parts)
        payload["member_oid"] = str(d.get("senderId") or "") or None
        payload["member_name"] = payload.get("member_name") or d.get("senderTitle")
        return RawDisclosure(
            market=Market.TR, source=Source.KAP, source_id=str(idx), kind=DisclosureKind.KAP_PORTFOLIO_REPORT,
            published_at=published, raw_uri=d.get("link") or f"https://www.kap.org.tr/tr/Bildirim/{idx}", payload=payload,
        )


# ---------------------------------------------------------------------- helpers (pure)


def _html_of(detail: dict) -> str:
    """Concatenate whatever HTML the detail carries (htmlMessages / presentation / flat content)."""
    chunks: list[str] = []

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("tr", "content", "html") and isinstance(v, str):
                    chunks.append(v)
                else:
                    walk(v)
        elif isinstance(o, list):
            for x in o:
                walk(x)
        elif isinstance(o, str) and "<" in o and ">" in o and len(o) > 200:
            chunks.append(o)

    walk(detail)
    return "\n".join(chunks)


def _bracket_list(text: str, label: str) -> list[str]:
    m = re.search(re.escape(label) + r".{0,200}?\[([^\]]*)\]", text, flags=re.S)
    return [c.strip().upper() for c in m.group(1).split(",") if c.strip()] if m else []


def _rows_from_html(html: str):
    from instilens.ingestion.kap.public_adapter import _num, _pct

    rows, before, after, seen = [], None, None, set()
    i = html.find("Transaction Date")
    if i < 0:
        return rows, before, after
    for cells in _table_rows(html[i : i + 40000]):
        key = tuple(cells)
        if key in seen or len(cells) < 4 or not re.match(r"\d{2}/\d{2}/\d{4}", cells[0]):
            continue
        seen.add(key)
        d = datetime.strptime(cells[0], "%d/%m/%Y").date().isoformat()
        buy, sell = _num(cells[1]), _num(cells[2])
        before = before or (_pct(cells[6]) if len(cells) > 6 else None)
        after = _pct(cells[8]) if len(cells) > 8 else after
        if buy:
            rows.append({"transaction_date": d, "side": "ALIS", "nominal": buy})
        if sell:
            rows.append({"transaction_date": d, "side": "SATIS", "nominal": sell})
    return rows, before, after


def _time(value) -> datetime:
    if not value:
        return datetime.now()
    for fmt in ("%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%Y.%m.%d %H:%M:%S"):
        try:
            return datetime.strptime(str(value)[:19], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromtimestamp(int(value) / 1000)
    except (TypeError, ValueError, OSError):
        return datetime.now()


def probe(base_url: str, api_key: str, api_secret: str, dump: Callable[[str, object], None]) -> None:
    """One-off connectivity check used by `instilens kap-test`: 3 calls, dumps raw JSON for inspection."""
    a = KapApiAdapter(base_url, api_key, api_secret)
    last = a.last_index()
    dump("lastDisclosureIndex", last)
    page = a.list_from(max(last - 60, 1))
    dump("disclosures", page[:5])
    tx = next((r for r in page if (r.get("title") or "").strip() == TX_TITLE), page[0] if page else None)
    if tx:
        dump("disclosureDetail(html)", a.detail(int(tx["disclosureIndex"]), "html"))
        dump("disclosureDetail(data)", a.detail(int(tx["disclosureIndex"]), "data"))
