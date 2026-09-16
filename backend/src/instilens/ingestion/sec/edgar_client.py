"""SEC EDGAR client for Form 13F-HR. Free, no contract — only a descriptive User-Agent is required.

Flow per filer (CIK):
  submissions JSON → recent 13F-HR accessions → filing index → information table XML → holdings.
A 13F is a *portfolio snapshot* of the filer at quarter end, so it feeds the same
`KAP_PORTFOLIO_REPORT`-style path: snapshot → diff → INFERRED position changes.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from xml.etree import ElementTree

import httpx

from instilens.domain.enums import DisclosureKind, Market, Source
from instilens.domain.schemas import RawDisclosure

SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:0>10}.json"
ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc_nodash}/"
NS = {"n": "http://www.sec.gov/edgar/document/thirteenf/informationtable"}
AMENDMENT_TYPE = re.compile(r"<(?:\w+:)?amendmentType>\s*([^<]+?)\s*</", re.I)
# Cover-page amendment types. RESTATEMENT replaces the original information table; NEW HOLDINGS only lists the
# positions the original left out, so it completes the original filing instead of superseding it.
ADDITIVE_AMENDMENT = "NEW HOLDINGS"


class EdgarClient:
    name = "sec-edgar"

    def __init__(self, user_agent: str, client: httpx.Client | None = None) -> None:
        # SEC blocks anonymous clients; the UA must identify the app and a contact address.
        self.client = client or httpx.Client(headers={"User-Agent": user_agent, "Accept-Encoding": "gzip"}, timeout=30, follow_redirects=True)

    def recent_13f(self, cik: str, limit: int = 4) -> list[dict]:
        data = self.client.get(SUBMISSIONS.format(cik=int(cik))).raise_for_status().json()
        recent = data["filings"]["recent"]
        out = []
        for form, acc, filed, period in zip(recent["form"], recent["accessionNumber"], recent["filingDate"], recent["reportDate"], strict=False):
            if form in ("13F-HR", "13F-HR/A"):
                out.append({"cik": str(int(cik)), "name": data.get("name", ""), "accession": acc, "filed": filed, "period": period, "amendment": form.endswith("/A")})
            if len(out) >= limit:
                break
        return out

    def information_table(self, cik: str, accession: str) -> list[dict]:
        base = ARCHIVE.format(cik=int(cik), acc_nodash=accession.replace("-", ""))
        index = self.client.get(base).raise_for_status().text
        candidates = [m for m in re.findall(r'href="([^"]+\.xml)"', index) if "primary_doc" not in m.lower()]
        if not candidates:
            raise ValueError(f"no information table xml in {base}")
        xml = self.client.get("https://www.sec.gov" + candidates[0] if candidates[0].startswith("/") else base + candidates[0].rsplit("/", 1)[-1]).raise_for_status().text
        return parse_information_table(xml)

    def amendment_type(self, cik: str, accession: str) -> str | None:
        """Cover-page `amendmentType` of a 13F-HR/A (RESTATEMENT / NEW HOLDINGS); None when the cover page is
        unreadable, which the pipeline reads as the historical default, a restatement."""
        base = ARCHIVE.format(cik=int(cik), acc_nodash=accession.replace("-", ""))
        try:
            xml = self.client.get(base + "primary_doc.xml").raise_for_status().text
        except httpx.HTTPError:
            return None
        m = AMENDMENT_TYPE.search(xml)
        return m.group(1).upper() if m else None

    def fetch_filer(self, cik: str, limit: int = 4) -> list[RawDisclosure]:
        filings = self.recent_13f(cik, limit)
        for f in filings:
            if f["amendment"]:  # the cover page decides whether this amendment replaces the original
                f["amendment_type"] = self.amendment_type(cik, f["accession"])
        out = []
        for f in link_amendments(filings):
            holdings = self.information_table(cik, f["accession"])
            out.append(to_raw_disclosure(f, holdings))
        return out


def is_additive_amendment(filing: dict) -> bool:
    """True for a NEW HOLDINGS 13F-HR/A: it adds positions to the original, it does not restate it."""
    return bool(filing.get("amendment")) and str(filing.get("amendment_type") or "").strip().upper() == ADDITIVE_AMENDMENT


def link_amendments(filings: list[dict]) -> list[dict]:
    """Give each restating 13F-HR/A its original: the latest earlier filing of the same filer for the same period
    (`amends` = that accession). The pipeline also resolves originals stored in earlier runs; this covers the
    common case where both sit in one submissions batch, so the amendment supersedes on first ingest.
    A NEW HOLDINGS amendment names no original — it completes one, and must not supersede it."""
    ordered = sorted(filings, key=lambda f: (f["filed"], f["accession"]))
    out = []
    for i, f in enumerate(ordered):
        amends = None
        if f.get("amendment") and not is_additive_amendment(f):
            earlier = [g for g in ordered[:i] if g["cik"] == f["cik"] and g["period"] == f["period"]]
            amends = earlier[-1]["accession"] if earlier else None
        out.append({**f, "amends": amends})
    return out


def parse_information_table(xml: str) -> list[dict]:
    root = ElementTree.fromstring(xml)
    rows = []
    for it in root.iter(f"{{{NS['n']}}}infoTable"):
        g = lambda tag, el=it: (el.findtext(f"n:{tag}", namespaces=NS) or "").strip()  # noqa: E731
        shares = it.find("n:shrsOrPrnAmt", NS)
        rows.append({
            "issuer": g("nameOfIssuer"), "class": g("titleOfClass"), "cusip": g("cusip").upper(),
            "value_usd": int(g("value") or 0), "quantity": int((shares.findtext("n:sshPrnamt", namespaces=NS) or "0").replace(",", "")) if shares is not None else 0,
            "put_call": g("putCall"),
        })
    return rows


def to_raw_disclosure(filing: dict, holdings: list[dict]) -> RawDisclosure:
    # Shares only (no options), aggregated per CUSIP — the same issuer can appear on several rows.
    merged: dict[str, dict] = {}
    for h in holdings:
        if h["put_call"]:
            continue
        m = merged.setdefault(h["cusip"], {"cusip": h["cusip"], "issuer": h["issuer"], "quantity": 0, "value_usd": 0})
        m["quantity"] += h["quantity"]
        m["value_usd"] += h["value_usd"]
    total = sum(m["value_usd"] for m in merged.values()) or 1
    return RawDisclosure(
        market=Market.US, source=Source.SEC, source_id=filing["accession"], kind=DisclosureKind.SEC_13F,
        published_at=datetime.fromisoformat(filing["filed"]),
        raw_uri=ARCHIVE.format(cik=filing["cik"], acc_nodash=filing["accession"].replace("-", "")),
        payload={
            "cik": filing["cik"], "filer_name": filing["name"], "period": filing["period"], "amendment": filing["amendment"],
            "amendment_type": filing.get("amendment_type"), "amends_source_id": filing.get("amends"),
            "holdings": [{**m, "weight_pct": round(m["value_usd"] / total * 100, 4)} for m in merged.values()],
        },
    )


def quarter_end(d: str | date) -> date:
    d = date.fromisoformat(d) if isinstance(d, str) else d
    return d
