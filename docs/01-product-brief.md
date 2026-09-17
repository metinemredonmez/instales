# 01 · Product brief

**Working name:** InstiLens · *See where smart money moves.* / *Profesyonel paranın nereye gittiğini gör.*
(Preliminary naming. Trademark/domain clearance still required. For the Turkish consumer surface a
Turkish-readable brand can sit on top — InstiLens stays the company/global name.)

**Category:** Institutional & smart-money intelligence platform. Not a fund-tracking app.

| Fintables answers | InstiLens answers |
|---|---|
| What does TMV hold? | What is TMV *accumulating*, since when, with how much conviction? |
| How much of SASA do funds hold? | Which 14 funds increased ASELS this month, while the price fell 12%? |
| | Which stocks got 6 first-time fund positions this period? Which lost 11 funds entirely? |

## Two universes, one engine
| | 🇹🇷 Türkiye (MVP) | 🌍 Global (v2) |
|---|---|---|
| Source | KAP (Pay Alım Satım Bildirimi, Portföy Dağılım Raporu) | SEC EDGAR (13F, Form 4), ETF holdings |
| Actor | PYŞ → funds | Institutions, hedge funds, superinvestors, insiders, ETFs |
| Freshness | some events same-day | 13F up to 45 days late — shown to the user, always |

Same schema, same engines; `market` is a column, not a fork.

## MVP screens (8)
1. Smart Money Radar (home) — today/7D/30D/3M: most accumulated, most distributed, new positions, exits, live events, consensus
2. Live KAP Radar — SSE feed of normalized transactions with confidence badge and source link
3. Stock Intelligence — scores + "why", buyers/sellers, price vs institutional holdings, signals, timeline
4. Fund Intelligence — holdings, NEW/ADD/REDUCE/EXIT, overlap
5. Institution (PYŞ) page
6. Smart Money Screener
7. Watchlist
8. Alerts

**AI Research** (natural-language questions over the same data) ships with the MVP as a feature,
not as the engine: the deterministic layer is the product, the model is the interface to it.

## Not in the MVP
Trading, orders, broker integration, native mobile, news/social sentiment, technical analysis, crypto,
politician/dark-pool/options data. (Portfolio *tracking* — the user's own positions beside the institutional
context — shipped in Faz 7 as a paid feature; it records what the user holds, it never places an order.)

## Monetization (after free beta)
Three tiers, one matrix — `backend/src/instilens/services/plans.FEATURES` is the source and the plan page shows
it as it stands there (docs/06 decision 14). Every plan reads the radar, scores, signals, screener and the morning
brief. Free: 10 watchlist items, 3 alert rules, 5 AI research questions a day. Pro: portfolio tracking (1 portfolio,
100 positions), narration, push, 100 items / 50 rules / 50 questions. Pro+: 5 portfolios, organisation seats (10),
500 items / 500 rules / 300 questions. Plus a B2B data feed / API for brokers and research desks (not in the matrix
yet). Gating is a runtime switch, off until payments are open.

## Legal boundary
Descriptive statistics about public disclosures — never recommendations. SPK investment-advice review
before commercial launch. Every number links to its source disclosure.

## The moat
Not the UI, not the parser. It is the **historical normalized institutional dataset with lineage,
entity resolution, position reconstruction, confidence, and signal/score history** — plus
`signal_outcomes`, which lets us prove (or disprove) that the signals work.
