# fixtures/sec — real EDGAR documents

Everything here is a real SEC filing, downloaded once (public data, no licence) and replayed by the tests. Nothing
is invented; the only edits are the trims described below. Every request that produced a file carried the SEC user
agent from `config.py` (`sec_user_agent`) and the scripts pace themselves under EDGAR's fair-use ceiling (5 req/s).

## 13F-HR — `*.json` (top level)

Canonical `RawDisclosure` documents (`domain/schemas.py`) as `EdgarClient.fetch_filer` produces them: the cover data
plus the parsed information table (shares only, aggregated per CUSIP). Replayed by `SecFixtureAdapter`, which globs
`*.json` in this directory only — keep other JSON out of the top level.

| Accession | Filer | Period | Source |
|---|---|---|---|
| `0001193125-26-054580`, `-226661`, `-352200` | Berkshire Hathaway (CIK 1067983) | 2025-12-31, 2026-03-31, 2026-06-30 | `https://www.sec.gov/Archives/edgar/data/1067983/<accession without dashes>/` |
| `0001350694-26-000001/2/3` | Bridgewater Associates (CIK 1350694) | same three quarters | `https://www.sec.gov/Archives/edgar/data/1350694/<accession without dashes>/` |
| `0001037389-26-000023/33/59` | Renaissance Technologies (CIK 1037389) | same three quarters | `https://www.sec.gov/Archives/edgar/data/1037389/<accession without dashes>/` |

Regenerate with `uv run python scripts/build_sec_fixtures.py` (also refreshes `../cusips_US.csv`, the OpenFIGI
CUSIP → ticker map of the largest positions).

## Form 4 (insider transactions) — `form4/`

Replayed by `tests/test_insiders.py` through an `httpx.MockTransport`, so `EdgarClient` runs its real code paths
without the network. Regenerate with `uv run python scripts/build_form4_fixtures.py`.

| File | What it is | Source URL |
|---|---|---|
| `0001140361-26-036226.form4.xml` | Apple Inc. (CIK 320193): Form 4 of Jennifer Newstead (SVP, GC) — one open-market **sale** (code S, 1,438 sh @ 317.23) under a 10b5-1 plan, one footnote | `https://www.sec.gov/Archives/edgar/data/320193/000114036126036226/form4.xml` |
| `0001140361-26-025622.form4.xml` | Apple Inc.: Form 4 of the same officer — RSU settlement (code **M**, no price: footnote only), shares withheld for tax (code **F** @ 296.42), a derivative-table row (RSU, code M) and three footnotes | `https://www.sec.gov/Archives/edgar/data/320193/000114036126025622/form4.xml` |
| `0001628280-26-045313.form4.xml` | Occidental Petroleum (CIK 797468): Form 4 of Richard A. Jackson (President and CEO, also a director) — one open-market **purchase** (code P, 4,770 sh @ 52.38), an indirect holding row (401(k) plan, not a transaction), an empty derivative table | `https://www.sec.gov/Archives/edgar/data/797468/000162828026045313/wk-form4_1782342238.xml` |
| `0001193125-26-333151.form4.xml` | DaVita (CIK 927066): a **joint filing** — two reporting owners, Berkshire Hathaway Inc and Warren E. Buffett, both flagged 10 % owner, neither director nor officer — one sale (code S, 182,980 sh @ 199.548, indirect), two footnotes. The group-filing path: one set of rows, several owners | `https://www.sec.gov/Archives/edgar/data/927066/000119312526333151/ownership.xml` |
| `CIK0000320193.submissions.json` | Apple's submissions listing, **trimmed** to 16 of its 1,000 `recent` filings: the 8-K / 10-K / 10-Q filed since 2025-10-01 and four Form 4s — the two above plus two of 2026-04-03 (no XML kept: they sit outside every test window). The `recent` block keeps EDGAR's columnar shape; `files` (the one archived chunk, 1994–2015) is as published. | `https://data.sec.gov/submissions/CIK0000320193.json` |
| `CIK0000797468.submissions.json` | Occidental's listing, trimmed the same way to 14 filings of 2026 (one Form 4: the purchase above); `files` (one chunk, 1995–2016) as published | `https://data.sec.gov/submissions/CIK0000797468.json` |
| `company_tickers.excerpt.json` | The SEC ticker → CIK map, **trimmed** to the 179 rows whose ticker appears in `../cusips_US.csv` (the instruments the 13F fixtures create); keys and values are verbatim (`cik_str`, `ticker`, `title`) | `https://www.sec.gov/files/company_tickers.json` |

Four Form 4 documents rather than two because no single small filing shows every shape the parser must handle:
an S and a P code, a missing price (RSU settlement), a derivative-table row, an indirect holding row, footnotes,
and a joint filing with two reporting owners.
No 4/A is kept — issuers file them rarely and the closest ones are from 2022–2023 — so the amendment test replays
a real Form 4 under a 4/A listing entry, the way `test_sec.py` builds a 13F-HR/A from a real 13F-HR.
