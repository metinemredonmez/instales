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
        auth_header: str | None = None,
        auth_mode: str = "auto",
    ) -> None:
        # Gateway (Apinizer) accepts one of: Basic key:secret, an API-key header, or a token. Which one the
        # portal configured is not visible in the UI, so "auto" probes the candidates once on first use.
        self.base_url, self.api_key, self.api_secret, self.auth_header, self.auth_mode = base_url.rstrip("/"), api_key, api_secret, auth_header, auth_mode
        self.client = client or httpx.Client(base_url=self.base_url, timeout=45, headers={"Accept": "application/json"})
        self._configured = False
        self.min_interval = 60.0 / max(rate_per_min, 1)
        self.max_calls, self.pys_only, self.start_index = max_calls, pys_only, start_index
        self.known = known_source_ids or set()
        self._last_call = 0.0
        self._calls = 0

    # ------------------------------------------------------------------ auth
    def auth_candidates(self) -> list[tuple[str, dict, tuple | None]]:
        k, sec = self.api_key, self.api_secret
        cands: list[tuple[str, dict, tuple | None]] = []
        if self.auth_header:
            v = self.auth_header if " " in self.auth_header else f"Bearer {self.auth_header}"
            cands.append(("authorization", {"Authorization": v}, None))
        if k:
            cands += [
                ("basic", {}, (k, sec)),
                ("apikey", {"apikey": k}, None),
                ("apikey+secret", {"apikey": k, "apisecret": sec}, None),
                ("x-api-key", {"x-api-key": k}, None),
                ("x-api-key+secret", {"x-api-key": k, "x-api-secret": sec}, None),
                ("bearer-key", {"Authorization": f"Bearer {k}"}, None),
                ("bearer-secret", {"Authorization": f"Bearer {sec}"}, None),
                ("api-key", {"api-key": k}, None),
                ("apiKey", {"apiKey": k, "apiSecret": sec}, None),
            ]
        if self.auth_mode != "auto":
            cands = [c for c in cands if c[0] == self.auth_mode] or cands
        return cands

    def _apply(self, headers: dict, basic: tuple | None) -> None:
        for h in ("Authorization", "apikey", "apisecret", "x-api-key", "x-api-secret", "api-key", "apiKey", "apiSecret"):
            self.client.headers.pop(h, None)
        self.client.headers.update(headers)
        self.client.auth = httpx.BasicAuth(*basic) if basic else None

    def detect_auth(self) -> str:
        """Try candidates against /lastDisclosureIndex until one returns 200; sticks with it."""
        last_err = "no credentials configured"
        for name, headers, basic in self.auth_candidates():
            self._apply(headers, basic)
            time.sleep(self.min_interval)
            r = self.client.get("/lastDisclosureIndex")
            self._calls += 1
            if r.status_code == 200 and "lastDisclosureIndex" in r.text:
                self.auth_mode = name
                self._configured = True
                return name
            last_err = f"{name}: HTTP {r.status_code} {r.text[:120]}"
        raise RuntimeError(f"KAP API authentication failed — last attempt {last_err}")

    # ------------------------------------------------------------------ transport
    def _get(self, path: str, **params) -> httpx.Response:
        if not self._configured:
            self.detect_auth()
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
        d = self.detail(idx, "data")
        sender = d.get("senderTitle") or d.get("behalfSenderTitle") or ""
        if self.pys_only and "PORTFÖY" not in sender.upper():
            return None
        html = _html_of(d)
        text = html_mod.unescape(re.sub(r"<[^>]+>", "|", html))
        related = [x.get("code") if isinstance(x, dict) else str(x) for x in (d.get("relatedStocks") or [])]
        # relatedStocks mixes the subject company and the funds; funds are 3-letter TEFAS codes, the company is not.
        sender_codes = {c.upper() for c in (d.get("senderExchCodes") or [])}
        companies = _bracket_list(text, "İlgili Şirketler") or [c for c in related if len(c) > 3 and c.upper() not in sender_codes]
        funds = _bracket_list(text, "İlgili Fonlar") or [c for c in related if len(c) == 3]
        if not funds and d.get("behalfFundCode"):
            funds = [d["behalfFundCode"]]
        if not companies:
            return None
        rows, before, after = rows_from_data(d)
        numbers_from = "data"
        if not rows:
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
                "amends_source_id": str(d["relatedDisclosureIndex"]) if d.get("relatedDisclosureIndex") and is_correction(d) else None,
                "is_correction": is_correction(d),
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


# ---------------------------------------------------------------------- structured ("data") format


def flatten_report_items(detail: dict) -> list[dict]:
    """Walk presentation[].content.ReportItem recursively → [{name, context, value, measures}]."""
    out: list[dict] = []

    def walk(item):
        if isinstance(item, list):
            for x in item:
                walk(x)
            return
        if not isinstance(item, dict):
            return
        name = item.get("name")
        values = item.get("Values", {}).get("Value") if isinstance(item.get("Values"), dict) else None
        if name and values is not None:
            for v in values if isinstance(values, list) else [values]:
                if not isinstance(v, dict):
                    continue
                m = v.get("Measures", {}).get("Measure") if isinstance(v.get("Measures"), dict) else None
                measures = m if isinstance(m, list) else ([m] if m else [])
                out.append({"name": name, "context": v.get("contextId"), "value": v.get("value"), "measures": {x.get("measureName"): x.get("measureValueName") for x in measures if isinstance(x, dict)}})
        walk(item.get("ReportItem"))

    for p in detail.get("presentation") or []:
        walk((p.get("content") or {}).get("ReportItem"))
    return out


def rows_from_data(detail: dict):
    """Share-transaction line items → rows/before/after. Column names matched loosely (taxonomy wording varies)."""
    from instilens.ingestion.kap.public_adapter import _num, _pct

    by_ctx: dict[str, dict] = {}
    for it in flatten_report_items(detail):
        if it["measures"].get("LanguageOptionAxis") == "EnglishMember":
            continue
        n = (it["name"] or "").lower()
        if any(k in n for k in ("transactiondate", "purchased", "sold", "netnominal", "ratioofshares", "ratioofvoting", "beginningofday", "endofday")):
            by_ctx.setdefault(it["context"] or "", {})[n] = it["value"]
    rows, before, after = [], None, None
    for ctx in sorted(by_ctx):
        c = by_ctx[ctx]
        date_v = next((v for k, v in c.items() if "transactiondate" in k), None)
        if not date_v or not re.search(r"\d{2}[./]\d{2}[./]\d{4}", str(date_v)):
            continue
        d = datetime.strptime(re.search(r"\d{2}[./]\d{2}[./]\d{4}", str(date_v)).group(0).replace(".", "/"), "%d/%m/%Y").date().isoformat()
        buy = _num(str(next((v for k, v in c.items() if "purchased" in k), "0") or "0"))
        sell = _num(str(next((v for k, v in c.items() if "sold" in k), "0") or "0"))
        b = next((v for k, v in c.items() if "ratioofsharesowned" in k and "beginning" in k), None)
        e = next((v for k, v in c.items() if "ratioofsharesowned" in k and "end" in k), None)
        before = before or (_pct(str(b)) if b else None)
        after = _pct(str(e)) if e else after
        if buy:
            rows.append({"transaction_date": d, "side": "ALIS", "nominal": buy})
        if sell:
            rows.append({"transaction_date": d, "side": "SATIS", "nominal": sell})
    return rows, before, after


def is_correction(detail: dict) -> bool:
    return any(
        "correction" in (it["name"] or "").lower() and str(it["value"]).lower().startswith("evet")
        for it in flatten_report_items(detail)
    ) or detail.get("disclosureReason") in ("CORRECTION", "DUZELTME")


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


def probe(base_url: str, api_key: str, api_secret: str, dump: Callable[[str, object], None], auth_header: str | None = None, auth_mode: str = "auto") -> None:
    """One-off connectivity check used by `instilens kap-test`: auth detection + 4 calls, raw JSON dumped."""
    a = KapApiAdapter(base_url, api_key, api_secret, auth_header=auth_header, auth_mode=auth_mode)
    dump("auth_mode", a.detect_auth())
    last = a.last_index()
    dump("lastDisclosureIndex", last)
    page = a.list_from(max(last - 60, 1))
    dump("disclosures", page[:5])
    tx = None
    cursor = max(last - 60, 1)
    for _ in range(8):  # walk back up to ~400 disclosures looking for a share-transaction filing
        tx = next((r for r in page if (r.get("title") or "").strip() == TX_TITLE), None)
        if tx:
            break
        cursor = max(cursor - 50, 1)
        page = a.list_from(cursor)
    if tx:
        detail = a.detail(int(tx["disclosureIndex"]), "data")
        dump("tx_index", tx["disclosureIndex"])
        dump("tx_detail(data)", detail)
        dump("tx_rows_parsed", rows_from_data(detail))
        dump("tx_items", [(i["name"], i["value"]) for i in flatten_report_items(detail)][:40])
    else:
        dump("tx", "no Pay Alım Satım Bildirimi found in the scanned range")
