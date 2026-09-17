# Fixtures — TEST DATA ONLY

The application never loads these; `instilens run` uses real sources. `kap/` is synthetic (tests);
`sec/` are real 13F filings (and, under `sec/form4/`, real Form 4 documents + trimmed submissions listings — see
`sec/README.md`), `kap_public/` are real KAP pages/PDFs (PYŞ filings, a fund report and four insider filings — see
`kap_public/README.md`), `cusips_US.csv` is a real OpenFIGI map.

`kap/*.json` and `prices_TR.csv` are invented for development and tests: placeholder funds, companies and
numbers, **not** real disclosures. `sec/` and `kap_public/` are the opposite — real public filings reproduced
verbatim from EDGAR / kap.org.tr, including the named parties and their transactions exactly as published, kept
for parser tests only (never loaded by the application; sources in each folder's README).

- `kap/*.json` — canonical `RawDisclosure` documents (see `instilens/domain/schemas.py`).
  - `16010xx` portfolio reports for 5 funds × 4 month-ends
  - `1607900` transaction already covered by a later snapshot (de-dup test)
  - `1608319` GROUPED buy, `1608402` EXACT buy, `1608450` amendment of 1608319, `1608500` GROUPED sell
- `prices_TR.csv` — closes used for flow valuation, divergence and signal outcomes.

Regenerate with the script in the session scratchpad or write a new one; do not hand-edit values
in a way that breaks the narrative the tests assert (ASELS accumulation + positive divergence,
THYAO new-position cluster, SASA exit cluster).
