# Fixtures — TEST DATA ONLY

The application never loads these; `instilens run` uses real sources. `kap/` is synthetic (tests);
`sec/` are real 13F filings, `kap_public/` are real KAP pages/PDFs, `cusips_US.csv` is a real OpenFIGI map.

Everything in this directory is invented for development and tests. Fund codes and company names
are placeholders; quantities, prices and ownership percentages are **not** real disclosures.

- `kap/*.json` — canonical `RawDisclosure` documents (see `instilens/domain/schemas.py`).
  - `16010xx` portfolio reports for 5 funds × 4 month-ends
  - `1607900` transaction already covered by a later snapshot (de-dup test)
  - `1608319` GROUPED buy, `1608402` EXACT buy, `1608450` amendment of 1608319, `1608500` GROUPED sell
- `prices_TR.csv` — closes used for flow valuation, divergence and signal outcomes.

Regenerate with the script in the session scratchpad or write a new one; do not hand-edit values
in a way that breaks the narrative the tests assert (ASELS accumulation + positive divergence,
THYAO new-position cluster, SASA exit cluster).
