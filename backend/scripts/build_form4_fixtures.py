"""Pull the real Form 4 / submissions documents the insider tests replay from EDGAR into fixtures/sec/form4.

Usage: uv run python scripts/build_form4_fixtures.py
Public data; re-run any time. Every request carries the SEC user agent from config and the script sleeps
between requests (≤ 5 req/s is the published fair-use ceiling). The documents are stored verbatim except the
submissions JSON files, which are trimmed to the filings the tests need (the `recent` block keeps its columnar
shape, `files` stays as published); fixtures/sec/README.md lists the source URL of every file.
"""

import json
import time
from pathlib import Path

from instilens.config import settings
from instilens.ingestion.sec.edgar_client import ARCHIVE, SUBMISSIONS, TICKERS, EdgarClient

out = Path("fixtures/sec/form4")
out.mkdir(parents=True, exist_ok=True)
client = EdgarClient(settings.sec_user_agent).client
PAUSE = 0.25

# Form 4 documents (issuer CIK, accession, primary document as the submissions JSON names it).
FORM4 = [
    ("320193", "0001140361-26-036226", "xslF345X06/form4.xml"),  # Apple: an open-market sale (S) under a 10b5-1 plan
    ("320193", "0001140361-26-025622", "xslF345X06/form4.xml"),  # Apple: RSU settlement (M) + tax withholding (F), derivative table
    ("797468", "0001628280-26-045313", "xslF345X06/wk-form4_1782342238.xml"),  # Occidental: CEO open-market purchase (P)
    ("927066", "0001193125-26-333151", "xslF345X06/ownership.xml"),  # DaVita: joint filing of Berkshire Hathaway + Warren Buffett (10% owner group), one sale (S)
]
# Submissions listings: which forms and, per issuer, which Form 4 accessions to keep (only the ones stored above,
# plus a few older ones so the days_back cut has something to drop).
KEEP_FORMS = ("4", "4/A", "8-K", "10-K", "10-Q")
SUBMISSION_RULES = {
    "320193": {"since": "2025-10-01", "form4": {"0001140361-26-036226", "0001140361-26-025622", "0001140361-26-013192", "0001140361-26-013191"}},
    "797468": {"since": "2026-01-01", "form4": {"0001628280-26-045313"}},
}
# company_tickers.json excerpt: the real rows of the tickers in fixtures/cusips_US.csv (the instruments the tests create).
TICKER_ROWS = {row.split(",")[1].strip().upper().replace("/", "-") for row in Path("fixtures/cusips_US.csv").read_text(encoding="utf-8").splitlines()[1:]}

for cik, accession, primary in FORM4:
    url = ARCHIVE.format(cik=int(cik), acc_nodash=accession.replace("-", "")) + primary.rsplit("/", 1)[-1]
    xml = client.get(url).raise_for_status().text
    (out / f"{accession}.form4.xml").write_text(xml, encoding="utf-8")
    print("saved", accession, url, len(xml), "bytes")
    time.sleep(PAUSE)

for cik, rule in SUBMISSION_RULES.items():
    data = client.get(SUBMISSIONS.format(cik=int(cik))).raise_for_status().json()
    recent = data["filings"]["recent"]
    keep = [
        i for i, form in enumerate(recent["form"])
        if form in KEEP_FORMS and recent["filingDate"][i] >= rule["since"] and (not form.startswith("4") or recent["accessionNumber"][i] in rule["form4"])
    ]
    trimmed = {k: data[k] for k in ("cik", "entityType", "sic", "sicDescription", "name", "tickers", "exchanges", "fiscalYearEnd") if k in data}
    # `files` (the archived chunks before the `recent` block) is kept verbatim: the listing parser warns when a window reaches past the block.
    trimmed["filings"] = {"recent": {col: [values[i] for i in keep] for col, values in recent.items()}, "files": data["filings"].get("files", [])}
    (out / f"CIK{int(cik):010d}.submissions.json").write_text(json.dumps(trimmed, indent=1), encoding="utf-8")
    print("saved submissions", cik, data["name"], len(keep), "of", len(recent["form"]), "filings")
    time.sleep(PAUSE)

tickers = client.get(TICKERS).raise_for_status().json()
excerpt = {k: v for k, v in tickers.items() if v["ticker"].upper() in TICKER_ROWS}
(out / "company_tickers.excerpt.json").write_text(json.dumps(excerpt, indent=1), encoding="utf-8")
print("saved company_tickers excerpt", len(excerpt), "of", len(tickers), "rows")
