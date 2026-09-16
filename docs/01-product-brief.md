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
Trading, orders, portfolio management, broker integration, native mobile, news/social sentiment,
technical analysis, crypto, politician/dark-pool/options data.

## Monetization (after free beta)
Free (delayed, basic pages) · Pro (live radar, scores, signals, screener, alerts) · Pro+ (AI research,
export, API, global) · B2B data feed / API for brokers and research desks.

## Legal boundary
Descriptive statistics about public disclosures — never recommendations. SPK investment-advice review
before commercial launch. Every number links to its source disclosure.

## The moat
Not the UI, not the parser. It is the **historical normalized institutional dataset with lineage,
entity resolution, position reconstruction, confidence, and signal/score history** — plus
`signal_outcomes`, which lets us prove (or disprove) that the signals work.
