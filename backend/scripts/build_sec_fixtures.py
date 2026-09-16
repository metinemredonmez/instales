"""Pull real 13F-HR filings from EDGAR into fixtures/sec and map CUSIPs to tickers via OpenFIGI.

Usage: uv run python scripts/build_sec_fixtures.py [--filings 3]
Public data; re-run any time. OpenFIGI is rate-limited without a key (batches of 10, ~25 req/min).
"""

import csv
import json
import sys
import time
from pathlib import Path

import httpx

from instilens.config import settings
from instilens.ingestion.sec.edgar_client import EdgarClient

FILERS = settings.sec_ciks
N = int(sys.argv[sys.argv.index("--filings") + 1]) if "--filings" in sys.argv else 3
out = Path("fixtures/sec")
out.mkdir(parents=True, exist_ok=True)
client = EdgarClient(settings.sec_user_agent)

TOP_N = 60  # map only the largest positions per filing; the rest stay as unverified CUSIP instruments
cusips: dict[str, str] = {}
if "--map-only" not in sys.argv:
    for cik in FILERS:
        for raw in client.fetch_filer(cik, N):
            (out / f"{raw.source_id}.json").write_text(raw.model_dump_json(indent=1), encoding="utf-8")
            print("saved", raw.source_id, raw.payload["filer_name"], raw.payload["period"], len(raw.payload["holdings"]), "holdings")
            time.sleep(0.3)
for path in out.glob("*.json"):
    payload = json.loads(path.read_text())["payload"]
    for h in sorted(payload["holdings"], key=lambda h: -h["value_usd"])[:TOP_N]:
        cusips.setdefault(h["cusip"], h["issuer"])

print("mapping", len(cusips), "cusips via OpenFIGI …")
rows = []
items = list(cusips.items())
figi = httpx.Client(timeout=30)
for i in range(0, len(items), 10):
    batch = items[i : i + 10]
    r = figi.post("https://api.openfigi.com/v3/mapping", json=[{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c, _ in batch])
    if r.status_code == 429:
        time.sleep(15)
        r = figi.post("https://api.openfigi.com/v3/mapping", json=[{"idType": "ID_CUSIP", "idValue": c, "exchCode": "US"} for c, _ in batch])
    r.raise_for_status()
    for (cusip, issuer), res in zip(batch, r.json(), strict=False):
        data = res.get("data") or []
        if data:
            rows.append({"cusip": cusip, "symbol": data[0]["ticker"], "name": data[0].get("name") or issuer})
    time.sleep(2.6)
with open("fixtures/cusips_US.csv", "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=["cusip", "symbol", "name"])
    w.writeheader()
    w.writerows(rows)
print("mapped", len(rows), "/", len(cusips))
