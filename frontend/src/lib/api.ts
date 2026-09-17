/** Typed client for the InstiLens API. Shapes mirror backend/src/instilens/services/analytics.py. */

export type Market = "TR" | "US"
/** Score / activity window per market (mirrors backend MARKET_WINDOW_DAYS: 30D for BIST, 100D ≈ latest 13F quarter). */
export const MARKET_WINDOW_DAYS: Record<Market, number> = { TR: 30, US: 100 }
export type Confidence = "EXACT" | "GROUPED" | "INFERRED"
export type Activity = "NEW" | "ADD" | "REDUCE" | "EXIT" | "HOLD"
export type SignalType =
  | "ACCUMULATION"
  | "DISTRIBUTION"
  | "POSITIVE_DIVERGENCE"
  | "NEGATIVE_DIVERGENCE"
  | "NEW_POSITION_CLUSTER"
  | "EXIT_CLUSTER"
  /** ≥ 3 distinct insiders bought within 30 days — SEC Form 4 code P on US issuers, KAP pay alım satım bildirimi (ALIS) on BIST. */
  | "INSIDER_BUY_CLUSTER"

export interface RadarRow {
  symbol: string
  name: string
  smart_money_score?: number
  consensus_score?: number | null
  net_flow_value: number
  funds_increasing: number
  funds_reducing: number
  funds_new: number
  funds_exited: number
  confidence_multiplier?: number | null
}

export interface Signal {
  symbol: string
  type: SignalType
  strength: number
  window_start: string
  window_end: string
  confidence: Confidence
  evidence: Record<string, unknown>
}

export interface TimelineItem { date: string; kind: "PERIOD" | "EVENT" | "SIGNAL" | "SCORE"; title: string; detail: string | null; confidence: Confidence | null; signal_type?: SignalType }
export interface FundCompare {
  a: { code: string; name: string; institution: string }
  b: { code: string; name: string; institution: string }
  common: { symbol: string; a_weight_pct: number | null; b_weight_pct: number | null; a_move: Activity | null; b_move: Activity | null }[]
  only_a: string[]
  only_b: string[]
  both_increasing: string[]
  both_reducing: string[]
  opposite: { symbol: string; a: Activity; b: Activity }[]
  /** Common symbols / union × 100. */
  overlap_pct: number
  /** Σ min(weight_a, weight_b) over the common symbols; null when either fund's latest snapshot carries no weights. */
  overlap_pct_weighted: number | null
}

/**
 * /funds/overlap?codes=A,B,C — 2..6 funds of one market, each read at its latest snapshot. `pairwise` holds every
 * unordered pair once (symbol-based and weighted overlap, the weighted figure null when either fund lacks weights);
 * `common_all` is the symbols every requested fund holds, with each fund's weight, sorted by the smallest weight desc.
 * 404 when a code is unknown, 422 with fewer than two codes or mixed markets.
 */
export interface OverlapFund { code: string; name: string; institution: string; as_of: string | null; holdings: number }
export interface OverlapPair { a: string; b: string; overlap_pct_symbols: number; overlap_pct_weighted: number | null }
export interface FundOverlap {
  /** Newest of the funds' report dates; null when none has a snapshot yet. */
  as_of: string | null
  funds: OverlapFund[]
  pairwise: OverlapPair[]
  common_all: { symbol: string; name: string; weights: Record<string, number | null> }[]
}

/**
 * /stocks/{symbol}/ownership — who holds the stock, from each fund's LATEST snapshot only (one row per fund, no
 * double counting across dates), largest quantity first. Snapshots older than two reporting periods for the market
 * (TR 60 d, US 182 d) are left out of `holders` and counted in `stale_holders`. `totals.pct_of_shares` is
 * quantity / shares_outstanding × 100 and null while the share count is unknown; `top10_pct_of_held` is the ten largest
 * holders' share of the held quantity and `hhi` the Herfindahl index of holder quantities (0..10000). `crowding` is the
 * CROWDING score (engine/crowding.py) with its level and the per-component explanation; null when it has not been computed.
 */
export type CrowdingLevel = "low" | "medium" | "high"
/**
 * One crowding component as engine/crowding.py explains it (holders · held_pct · concentration · momentum): the raw
 * input, its 0..1 normalisation, the weight actually applied and the points it added to the 0..100 score. `skipped`
 * names why a component was left out — held_pct while the share count is unknown, its weight spread over the rest.
 * The engine adds the odd figure next to these (saturation, funds_increasing…), and `why` also carries plain
 * numbers such as `stale_holders`, which are context, not components.
 */
export interface CrowdingComponent { raw: number | null; contribution: number; normalized?: number | null; weight?: number; skipped?: string; [extra: string]: unknown }
export interface Crowding { score: number; level: CrowdingLevel; why: Record<string, CrowdingComponent | number | null> }
export interface OwnershipTotals {
  holders: number
  institutions: number
  quantity: number
  /** Σ of the values the reports state; null when none does. */
  market_value: number | null
  /** Holders whose report states no value — not in market_value. */
  unvalued_holders?: number
  pct_of_shares: number | null
  top10_pct_of_held: number | null
  hhi: number | null
}
export interface Holder {
  fund: string
  name: string
  institution: string
  quantity: number
  market_value: number | null
  /** Weight of the position inside that fund's portfolio. */
  weight_pct: number | null
  pct_of_shares: number | null
  as_of: string
  last_move: Activity | null
  last_move_period_end: string | null
  confidence: Confidence
}
/**
 * stock_detail.scores.CROWDING — the stored score row. `why` is the crowding engine's explanation (the `Crowding` shape
 * above, possibly with extra keys); `level` travels with the row when the API sends it and is otherwise read off the
 * score with the documented thresholds (low < 35, medium 35..65, high > 65).
 */
export interface CrowdingScore { score: number; raw?: number; level?: CrowdingLevel; why: Record<string, unknown> }
export interface Ownership {
  symbol: string
  name: string
  market: Market
  /** Latest snapshot date among the listed holders. */
  as_of: string | null
  shares_outstanding: number | null
  shares_as_of: string | null
  currency: string
  totals: OwnershipTotals
  crowding: Crowding | null
  holders: Holder[]
  stale_holders: number
}
export interface InstitutionRow { code: string; name: string; kind: string; funds: number; events: number; is_verified: boolean }
export interface InstitutionDetail {
  code: string; name: string; kind: string; is_verified: boolean
  funds: { code: string; name: string; fund_type: string | null }[]
  activity_period_end: string | null
  top_increased: { symbol: string; delta_qty: number; delta_value: number; funds_increasing: number; funds_reducing: number }[]
  top_reduced: { symbol: string; delta_qty: number; delta_value: number; funds_increasing: number; funds_reducing: number }[]
  events: TxEvent[]
}
export interface Freshness { source: string; cadence: string; last: string | null; delayed: boolean }
export interface SignalPerf { by_type: { signal_type: SignalType; count: number; avg_ret_7d: number | null; avg_ret_30d: number | null; avg_ret_90d: number | null; avg_max_drawdown: number | null }[] }
/**
 * `plan` / `plan_source` ("stripe" | "manual", null on FREE) / `plan_until` (an admin grant's expiry) are the account's own
 * row; `effective_plan` / `effective_source` what it actually gets (an organisation seat or an expired grant differ).
 */
export interface AdminUser {
  id: number; email: string; name: string; plan: string; role: string; is_active: boolean; created_at: string; last_login_at: string | null
  plan_source?: string | null; plan_until?: string | null; effective_plan?: string; effective_source?: string
}
export interface Review {
  instruments: { id: number; market: string; symbol: string; name: string }[]
  funds: { id: number; code: string; name: string; institution: string }[]
  institutions: { id: number; market: string; code: string; name: string }[]
  failed_disclosures: { id: number; source: string; source_id: string; kind: string; error: string | null }[]
}

export interface NewsItem {
  id: number; source: string; title: string; url: string; published_at: string; symbols: string[]; tags: string[]
  ai: { summary_tr: string; sector: string; sentiment: "positive" | "negative" | "neutral"; relevance: number; symbols?: string[] } | null
}
export interface NewsRule {
  id: number; name: string; market_code: Market; query: string; exclusion: string; only_sources: string; remove_sources: string
  language: string; max_age_days: number; symbols: string[]; newsapi_query: string; ai_summary: boolean; is_active: boolean
}

export interface AiNote {
  id: number; kind: string; subject: string; as_of: string; lang: "tr" | "en"; content: string
  headline: string; highlights: string[]; watch: string[]; headline_ids: number[]; confidence_note: string
  symbols: string[]; headlines: { id: number; title: string; source: string; url: string | null }[]; kap_base: string
  model: string; created_at: string
}

export interface NotifySettings { email: string; notify_email: boolean; notify_telegram_chat_id: string | null; notify_brief: boolean; brief_markets?: Market[]; lang: "tr" | "en"; channels: { telegram: boolean; email: boolean } }

export interface AuthConfig { allow_registration: boolean }
export interface MfaSetup { secret: string; otpauth_uri: string }

export interface PipelineState { running: boolean; started_at: string | null; finished_at: string | null; result: Record<string, number> | null; error: string | null }

export interface Radar {
  as_of: string | null
  window_days?: number
  window_start?: string
  accumulated: RadarRow[]
  distributed: RadarRow[]
  signals: Signal[]
}

export interface ActivitySummary {
  funds_increasing: number
  funds_reducing: number
  funds_unchanged: number
  funds_new: number
  funds_exited: number
  net_flow_value: string
  persistence_periods: number
  avg_conviction: number
  days_since_last_activity: number
  flow_by_confidence: Partial<Record<Confidence, string>>
}

export interface ScoreDetail {
  score: number
  raw: number
  why: {
    raw: number
    adjusted: number
    confidence_multiplier: number
    weights?: Record<string, number>
    components: Record<string, number>
    activity?: ActivitySummary
  }
}

export interface PositionChange {
  fund: string | null
  symbol: string
  activity: Activity
  period_start: string | null
  period_end: string
  from_qty: number
  to_qty: number
  delta_qty: number
  delta_value: number | null
  from_weight_pct: number | null
  to_weight_pct: number | null
  confidence: Confidence
}

export interface TxEvent {
  id: number
  market?: Market
  published_at: string
  effective_date: string
  symbol: string
  institution: string
  funds: string[]
  side: "BUY" | "SELL" | "MIXED"
  buy_nominal: number
  sell_nominal: number
  net_nominal: number
  net_value: number | null
  ownership_before_pct: number | null
  ownership_after_pct: number | null
  confidence: Confidence
  allocation: "EXACT" | "UNKNOWN"
  source: { name: string; id: string; uri: string | null }
}

export interface StockDetail {
  symbol: string
  name: string
  market: Market
  as_of: string | null
  /** CROWDING is stored like the others but explained by the crowding engine, so its `why` has its own shape. */
  scores: Partial<Record<"SMART_MONEY" | "CONSENSUS", ScoreDetail>> & { CROWDING?: CrowdingScore }
  latest_period_end: string | null
  top_buyers: PositionChange[]
  top_sellers: PositionChange[]
  signals: Signal[]
  events: TxEvent[]
  /** Valuation summary for the header chips; null until the fundamentals job has run for this symbol. */
  fundamentals: FundamentalsSummary | null
  /** Insider head-count for the header chip (Form 4 on US issuers, KAP filings on BIST); null until the issuer's filings have been read. */
  insiders: InsidersSummary | null
}

/**
 * /stocks/{symbol}/insiders — the issuer's insider transactions over the last `days` days (30..730, default 90),
 * newest first, at most 200. On US issuers these are SEC Form 4 rows of officers, directors and 10 % owners; on BIST
 * they are KAP "Pay Alım Satım Bildirimi" filings published under the issuer whose acting party is a natural person
 * or a controlling shareholder (`source` says which). Every row carries its filing: `accession` + `url` are the
 * EDGAR accession and index page on US, the KAP disclosure index and disclosure page on TR. `code` is the Form 4
 * transaction code as filed — KAP rows carry only P (ALIS) and S (SATIS); the UI labels the ten common ones and
 * prints the letter for anything else. `value` is shares × price and null when the filing reports no price (grants,
 * gifts) or only a price range (most KAP filings — `price_range` then carries [low, high], never a midpoint). A KAP
 * row where the acting party is the issuer itself is a buyback (`buyback` true, `role` "issuer"): listed with its
 * label, never a buyer, seller or cluster member in `summary`. `fetched_at` is when the source was last read (the
 * issuer's filings on EDGAR; the KAP feed, market-wide) and null until it has — an empty window then means "not read
 * yet", not "no activity". `coverage_since` (BIST only, null on US) is the earliest day the KAP feed has read: the
 * job lists a week per run, so an empty window that starts earlier is "nothing since that day", never "nothing in
 * 90 days". `truncated` says the window holds more rows than the 200 returned; `more_url` is where they all are — the
 * US issuer's Form 4 list on EDGAR (also `edgar_url`, the older name), the BIST issuer's page on KAP when a filing of
 * its own has told the server its oid, null otherwise. `supported: false` comes back for a symbol whose market has no
 * insider source, with nothing else populated.
 */
export type InsiderWindow = 90 | 180 | 365
export type InsiderCode = "P" | "S" | "A" | "M" | "F" | "G" | "D" | "C" | "X" | "J" | "W"
export type InsiderSource = "sec-edgar" | "kap"
/** KAP rows only: who the acting party is. A `company` is any legal entity — a controlling holding as much as the issuer itself; `buyback` says which. */
export type InsiderPartyKind = "person" | "company" | "fund" | "other"
export interface InsiderTx {
  id: number
  transaction_date: string
  filed_at: string
  insider: string
  insider_cik: string
  /** Comma-joined: director | officer | ten_percent_owner | other — KAP rows normalise "Görevi / Şirketle ilişkisi" to director | officer | shareholder | other (shareholder is what the filing states, not a stake size), and the issuer's own-share rows carry "issuer". */
  role: string
  /** Officer title as printed on the filing ("Chief Executive Officer"); null for a plain director or owner. */
  title: string | null
  code: InsiderCode | string
  /** The filing's (A)/(D) column: true for an acquisition. */
  acquired: boolean
  shares: number
  price: number | null
  /** KAP rows that state a range and no single price: [low, high] as filed; null otherwise (and always on Form 4 rows). */
  price_range: [number, number] | null
  value: number | null
  post_shares: number | null
  /** D = direct, I = indirect (through a trust, spouse, fund…). */
  ownership: "D" | "I"
  derivative: boolean
  /** Lineage: EXACT — the insider's own report of their own transaction. */
  confidence: string
  accession: string
  url: string
  /** KAP rows: the acting party's kind; null on Form 4 rows, which are always a person's. */
  party_kind: InsiderPartyKind | null
  /** The issuer trading its own shares (a buyback when `acquired`, a treasury-share sale otherwise): listed as filed, left out of the summary and the cluster. Always false on Form 4 rows. */
  buyback: boolean
  /** KAP rows: the stake after the transaction as the filing states it (percent of capital); null when it does not. */
  post_pct_stake: number | null
}
export interface InsiderCluster { since: string; insiders: number; value: number }
export interface InsiderStats {
  buyers: number
  sellers: number
  buy_value: number
  sell_value: number
  net_value: number
  open_market_buys: number
  open_market_sells: number
  /** Present when ≥ 3 distinct insiders bought on the open market within the 30 days ending `as_of`. */
  cluster: InsiderCluster | null
}
export type Insiders =
  | { supported: false; symbol?: string; name?: string; market?: Market; days?: number }
  | { supported: true; symbol: string; name: string; market: Market; days: number; as_of: string; source: InsiderSource; fetched_at: string | null; coverage_since: string | null; summary: InsiderStats; transactions: InsiderTx[]; truncated: boolean; edgar_url: string | null; more_url: string | null }
/** stock_detail.insiders — the 90-day head-count; `cluster` says whether the INSIDER_BUY_CLUSTER signal fired. */
export interface InsidersSummary { days: number; buyers: number; sellers: number; net_value: number; cluster: boolean }

/**
 * /stocks/{symbol}/filings — the issuer's latest EDGAR filings, newest first; `form` narrows to one form type.
 * `items` holds the 8-K item codes ("2.02") and is empty on other forms; `url` is the filing index page,
 * `primary_url` the main document when EDGAR names one. `fetched_at` is when the daily job last read the issuer and
 * null until it has — an empty list then means "not read yet", not "nothing filed". BIST symbols answer `supported: false`.
 */
export type FilingForm = "8-K" | "10-K" | "10-Q" | "4"
export interface Filing { form: string; filed_at: string; period: string | null; items: string[]; accession: string; url: string; primary_url: string | null }
export interface Filings { symbol: string; supported: boolean; fetched_at: string | null; filings: Filing[] }

/**
 * /stocks/{symbol}/fundamentals — Yahoo-sourced valuation snapshot, reported statements and ratios derived from them.
 * Money is absolute (never scaled) in one of two currencies: statement lines and the snapshot's TTM revenue / EBITDA /
 * net income are in `currency`, the filer's reporting currency; the snapshot's market cap, EV, 52-week range and EPS
 * are priced off the listing and are in `snapshot.quote_currency` (THYAO: statements in USD, market cap in TRY).
 * Percentages are already ×100, and anything Yahoo did not report is null: the UI prints a dash, never a zero.
 * `forward_pe` is the one figure based on consensus estimates rather than reported numbers — labelled as such.
 * Analyst recommendation fields are not part of the contract (SPK rule).
 */
export type FundamentalsPeriod = "annual" | "quarterly"
export interface FundamentalsSnapshot {
  as_of: string
  /** Listing currency of market_cap, enterprise_value, week52_* and eps_ttm. */
  quote_currency: string | null
  market_cap: number | null
  enterprise_value: number | null
  pe: number | null
  forward_pe: number | null
  price_to_book: number | null
  price_to_sales: number | null
  ev_to_ebitda: number | null
  profit_margin: number | null
  operating_margin: number | null
  return_on_assets: number | null
  return_on_equity: number | null
  revenue_ttm: number | null
  ebitda_ttm: number | null
  net_income_ttm: number | null
  eps_ttm: number | null
  dividend_yield: number | null
  payout_ratio: number | null
  beta: number | null
  week52_high: number | null
  week52_low: number | null
  shares_outstanding: number | null
  float_shares: number | null
  short_percent_of_float: number | null
}
export type IncomeKey = "revenue" | "cost_of_revenue" | "gross_profit" | "operating_income" | "ebitda" | "pretax_income" | "net_income" | "eps_diluted" | "interest_expense"
export type BalanceKey = "total_assets" | "total_liabilities" | "equity" | "total_debt" | "cash" | "current_assets" | "current_liabilities"
export type CashflowKey = "operating_cf" | "capex" | "free_cf" | "dividends_paid" | "share_repurchase"
export type StatementKey = IncomeKey | BalanceKey | CashflowKey
export type StatementKind = "income" | "balance" | "cashflow"
/** One reported period; `items` holds the canonical keys of its statement, null where the filing had no such line. */
export interface Statement { period_end: string; items: Partial<Record<StatementKey, number | null>> }
/** Newest statement vs the same period a year earlier; ratios as percentages (12.3 = 12.3 %). */
export interface Derived {
  gross_margin: number | null
  operating_margin: number | null
  net_margin: number | null
  fcf_margin: number | null
  debt_to_equity: number | null
  revenue_growth_yoy: number | null
  net_income_growth_yoy: number | null
  period_end: string | null
}
export interface Fundamentals {
  symbol: string
  name: string
  market: Market
  currency: string
  source: "yahoo"
  fetched_at: string | null
  snapshot: FundamentalsSnapshot | null
  period: FundamentalsPeriod
  /** Newest first, at most 8 per statement. */
  statements: Record<StatementKind, Statement[]>
  derived: Derived
}
export interface FundamentalsSummary {
  as_of: string
  market_cap: number | null
  pe: number | null
  price_to_book: number | null
  net_margin: number | null
  revenue_growth_yoy: number | null
  dividend_yield: number | null
  shares_outstanding: number | null
  /** Reporting currency (statements, net margin's basis). */
  currency: string
  /** Listing currency of market_cap; null when there is no snapshot yet. */
  quote_currency: string | null
  source: "yahoo"
}

export interface Holding {
  symbol: string
  quantity: number
  market_value: number | null
  weight_pct: number | null
}

export interface FundDetail {
  code: string
  name: string
  institution: { code: string; name: string }
  snapshot_as_of: string | null
  total_value: number | null
  holdings: Holding[]
  activity_period_end: string | null
  activity: Record<Exclude<Activity, "HOLD">, PositionChange[]>
  events: TxEvent[]
}

export interface RuntimeSetting { key: string; group: "access" | "ai" | "data"; type: string; min: number | null; max: number | null; value: unknown; default: unknown; overridden: boolean; updated_at: string | null; updated_by: string | null }

/**
 * /admin/warehouse — the DuckDB export of the public tables (docs/08-warehouse.md): whether the weekly Sunday build is
 * on (runtime setting `warehouse_enabled`), whether a build is running right now (`running`, with who started it
 * and when), the outcome of the last build (`last`, null before the first — `error` names a failed one), and every
 * dated file under releases_dir/warehouse newest first, `latest` being the latest.duckdb mirror. A build's `rows` is
 * the total from its _meta table and `row_counts` the per-table breakdown; a file whose _meta cannot be read (a
 * foreign or damaged file in the folder) is listed with `error` and null metadata rather than hiding the healthy
 * ones. `url` streams the file to an admin (bearer auth, so the UI fetches it and hands the blob to the browser
 * rather than linking it). The server keeps the newest `keep` dated builds.
 */
export interface WarehouseBuild {
  name: string
  size: number
  url: string
  built_at: string | null
  rows: number | null
  row_counts: Record<string, number> | null
  git_rev: string | null
  schema_version: number | null
  error: string | null
}
export interface WarehouseLast { started_by: string | null; started_at: string | null; finished_at: string; name: string | null; rows: number | null; seconds: number | null; error: string | null }
export interface WarehouseStatus { running: boolean; started_by: string | null; started_at: string | null; last: WarehouseLast | null }
export interface Warehouse extends WarehouseStatus { enabled: boolean; builds: WarehouseBuild[]; latest: WarehouseBuild | null; keep: number }

export interface DesktopFile { id: number; platform: string; label: string; kind: "INSTALLER" | "UPDATE"; filename: string; size: number; sha256: string; signed: boolean; downloads: number; downloadable: boolean; url: string }
export interface DesktopRelease { id: number; version: string; status: "DRAFT" | "PUBLISHED" | "WITHDRAWN"; notes: string; created_by: string | null; created_at: string; published_at: string | null; files: DesktopFile[] }

export interface TtsVoice { id: string; name: string; gender: "female" | "male"; lang: "tr" | "en"; source: "config" | "extra" | "library" }

export interface AuditEvent { id: number; kind: string; actor: string | null; subject: string | null; ip: string | null; detail: string | null; created_at: string }

export interface WaitlistRow { id: number; email: string; name: string | null; lang: string; source: string | null; created_at: string }

export interface AdminConfig {
  environment: string; public_url: string; allow_registration: boolean; database: string
  kap_adapter: string; kap_api_base_url: string | null; sec_adapter: string; sec_ciks: string[]
  ai: { provider: string; model: string; configured: boolean; news_enrich: boolean }
  tts: { provider: string | null; voices: Record<string, boolean> }
  news: { enabled: boolean; newsapi: boolean }
  channels: { telegram: boolean; email: boolean; web_push: boolean; onesignal: boolean }
}

/**
 * /admin/providers: the active price provider's status plus the feed process heartbeat (written to app_settings under
 * `_feed_status`; `running` is the server's verdict — last_run_at younger than 3 × interval_s). Every process has its
 * own provider instance, so `status` is the feed's view of it while the feed is alive (`status_from: "feed"` — that
 * process drives the strip) and the answering API worker's own instance otherwise (`"api"`). `selected` is the runtime
 * setting as chosen, `active` what actually answers (an unconfigured choice falls back to yahoo); `feed.error` is the
 * last iteration's failure, which may be the provider's own error or a publish/heartbeat problem of the feed.
 */
export interface ProviderStatus { name: string; configured: boolean; connected: boolean; delay: "realtime" | "delayed" | "eod"; last_tick_at: string | null; error: string | null; note: string | null }
export interface ProvidersStatus {
  price: { active: PriceSource; selected?: PriceSource; available: PriceSource[]; status: ProviderStatus; status_from?: "feed" | "api" }
  feed: { running: boolean; last_run_at: string | null; interval_s: number; published: number; provider?: string | null; error?: string | null }
}

export interface SearchHit {
  kind: "stock" | "fund" | "institution"
  key: string
  label: string
  name: string
  href: string
}

/** /search/text: full-text hits over disclosures, EDGAR filings, news and AI notes of one market (Postgres tsvector). */
export type TextKind = "disclosure" | "filing" | "news" | "note"
export interface TextHit {
  kind: TextKind
  id: number
  title: string
  /** ≤ 240 chars of plain text; the query terms come wrapped in «» so the UI can highlight them. */
  snippet: string
  date: string
  symbols: string[]
  /** "KAP" | "SEC" | the publisher | "InstiLens AI". */
  source: string
  /** The source page outside the app; null for AI notes. */
  url: string | null
  /** In-app route (e.g. /stocks/ASELS); null when the hit has no page of its own (AI notes: the page shows the current note). */
  link: string | null
  /** A disclosure a later correction replaced: kept, titled "Düzeltildi — / Superseded —", ranked after every live hit. */
  superseded: boolean
  score: number
}
/** `total` is the match count before the limit, capped at 500 by the API. */
export interface TextSearch { q: string; market: Market; total: number; hits: TextHit[] }

export interface ScreenerRow extends Omit<RadarRow, "confidence_multiplier" | "smart_money_score" | "consensus_score"> {
  smart_money_score: number
  consensus_score: number | null
  price_change_30d_pct: number | null
  signals: SignalType[]
}

export interface ScreenerFilters {
  min_smart_money_score?: number
  min_consensus_score?: number
  min_funds_increasing?: number
  min_funds_new?: number
  min_net_flow_value?: number
  max_price_change_30d_pct?: number
  signal?: SignalType[]
  limit?: number
}

export type MoveKind = "buys" | "sells" | "new" | "exits"
/** One fund (or, for a GROUPED event, its institution) behind a move; at most 5 per row, largest |delta_value| first. */
export interface MoveParty {
  kind: "fund" | "institution"
  code: string
  name: string
  activity: Exclude<Activity, "HOLD">
  delta_qty: number
  delta_value: number | null
  to_weight_pct: number | null
  delta_weight_pct: number | null
  period_end: string
  confidence: Confidence
}
export interface MoveRow {
  symbol: string
  name: string
  net_flow_value: number
  net_qty: number
  funds_increasing: number
  funds_reducing: number
  funds_new: number
  funds_exited: number
  party_count: number
  parties: MoveParty[]
}
export interface Moves {
  as_of: string
  window_days: number
  window_start: string
  kind: MoveKind
  market: Market
  fund: { code: string; name: string } | null
  /** Rows before the `limit` cut; the hint states this, not the page size. */
  total: number
  rows: MoveRow[]
}

export interface ResearchAnswer {
  question: string
  answer: string
  model: string
  usage: Record<string, number>
  tool_calls: { name: string; input: Record<string, unknown>; output_preview: string }[]
  /** Local engine only: figures in the answer no tool result carries (as the answer spells them); the answer text already opens with a sentence naming them and the page shows them in a warning band. Empty for Claude. */
  unverified_numbers: string[]
}

export interface StockSeries {
  symbol: string
  prices: { date: string; close: number }[]
  holdings: { date: string; quantity: number; funds: number }[]
}

/** Which price provider a quote came from; Yahoo prints are ~15 min delayed, so `delayed` travels with every quote. */
export type PriceSource = "yahoo" | "matriks"

/** Bar size of /stocks/{symbol}/candles: intraday from the provider (5m/15m ≤ 60 days, 1h ≤ 730 days on Yahoo), daily from market_prices. */
export type CandleInterval = "5m" | "15m" | "1h" | "1d"
/** One OHLC bar; `t` is epoch seconds (UTC), `v` null when the source reports no volume for it. */
export interface Candle { t: number; o: number; h: number; l: number; c: number; v: number | null }
/**
 * /stocks/{symbol}/candles — ascending bars, at most `lookback`. `source` / `delay` name whoever printed them and how far
 * behind they are (Yahoo bars ~15 min; `eod` for daily rows from market_prices, last night's close), `as_of` is the fetch
 * time and `tz` the market's own zone, which the widget renders in. `stale` is present (true) only when the server had
 * to serve its last good answer through an outage, like a header quote. 404 unknown symbol; 503 "provider unavailable"
 * while the provider slot cannot answer and nothing is remembered.
 */
export interface Candles { symbol: string; name: string; market: Market; currency: string; interval: CandleInterval; source: PriceSource; delay: ProviderStatus["delay"]; delayed: boolean; as_of: string; tz: string; bars: Candle[]; stale?: boolean }
/** Header quote (the active price provider via the backend/feed). Absent from the list when the source failed; `stale` marks a cached value. */
export interface Quote { key: string; label: string; price: number; change_pct: number | null; currency: string; updated_at: string; decimals: number; bar_date: string | null; stale?: boolean; source: PriceSource; delayed: boolean }
export type MarketState = "open" | "closed" | "pre" | "post"
export interface MarketStatus { state: MarketState; next_change_at: string; tz: string }
export interface QuotesResponse { as_of: string; quotes: Quote[]; markets: Partial<Record<Market, MarketStatus>> }

export interface WatchItem {
  id: number
  kind: "stock" | "fund"
  ref: string
  name: string
  market?: Market  // stocks only
  institution?: string
  smart_money_score?: number | null
  funds_increasing?: number | null
  funds_reducing?: number | null
  net_flow_value?: number
}

/**
 * Alert rule types the API accepts (its /alerts/rules answer lists the live set). SCORE_ABOVE carries `threshold`;
 * PRICE_ABOVE / PRICE_BELOW carry `price` in the market currency and are evaluated against the latest daily close of
 * market_prices (the header feed carries no stock quotes today), firing once per crossing — stocks only.
 */
export type RuleType = "NEW_FUND_POSITION" | "FUND_EXIT" | "KAP_TRANSACTION" | "SCORE_ABOVE" | "SIGNAL" | "FUND_ACTIVITY" | "INSIDER_BUY_CLUSTER" | "PRICE_ABOVE" | "PRICE_BELOW"
export type PriceRuleType = Extract<RuleType, "PRICE_ABOVE" | "PRICE_BELOW">
export type RuleParams = { threshold?: number; price?: number; since?: string } & Record<string, unknown>
/** Rules are owner-wide: the list holds every market's rules, so `market` is the subject's, not the page's. */
export interface AlertRule {
  id: number
  rule_type: RuleType | string
  params: RuleParams
  is_active: boolean
  symbol: string | null
  fund_code: string | null
  market: Market
}

export interface Notification {
  id: number
  title: string
  body: string
  link: string | null
  created_at: string
  read_at: string | null
}

/* ---------- plans, portfolios, organisations, billing (services/plans.py, portfolio.py, billing.py) ---------- */

export type PlanCode = "FREE" | "PRO" | "PRO_PLUS"
/** Where the effective plan comes from: the account's own subscription, an organisation seat, or an admin grant. */
export type PlanSource = "own" | "org" | "manual"
/**
 * /auth/me `features` — the plan's matrix as the API resolves it for this account: a boolean for an on/off feature
 * (portfolio, tts, push), a number for a cap (watchlist_items, alert_rules, ai_research_per_day…). The keys are the
 * API's; the UI reads the ones it knows and shows the rest by name on the plan page. Missing altogether on an API
 * that predates plans — then nothing is gated client-side and the 402 answer is the only signal.
 */
export type Features = Record<string, boolean | number>

/** One of the user's portfolios; `positions` is the row count the list carries. `market` is the listing market — positions are priced in its `currency`. */
export interface Portfolio { id: number; name: string; market: Market; currency: string; positions?: number; created_at: string }
/**
 * One held position with the institutional context the stock page shows: the latest stored close (null until the price
 * job has a bar), the P&L against the FIFO / entered average cost (null without a cost), the weight over the priced
 * positions, the three stored scores, the 30-day fund head-counts and — once the market's insider source has been
 * read (the issuer's Form 4s, the KAP feed) — the insiders' 90-day net value in the listing currency (null until
 * then). `derived` marks a row rewritten from transactions: it cannot be edited
 * by hand, only through another transaction.
 */
export interface PortfolioPosition {
  id: number
  symbol: string
  name: string
  derived: boolean
  quantity: number
  avg_cost: number | null
  opened_at: string | null
  note: string | null
  last_close: number | null
  close_date: string | null
  market_value: number | null
  cost_value: number | null
  pnl_value: number | null
  pnl_pct: number | null
  weight_pct: number | null
  smart_money_score: number | null
  consensus_score: number | null
  crowding_score: number | null
  funds_increasing_30d: number
  funds_reducing_30d: number
  insiders_net_90d: number | null
}
/** Totals over the priced positions; `unpriced` counts the ones without a stored close (not in market_value). */
export interface PortfolioTotals { market_value: number | null; cost_value: number | null; pnl_value: number | null; pnl_pct: number | null; unpriced?: number }
export interface PortfolioTransaction { id: number; symbol: string; side: "BUY" | "SELL"; quantity: number; price: number; traded_at: string; fee: number | null; note: string | null; created_at?: string }
export interface PortfolioDetail {
  portfolio: Portfolio
  /** Latest score date — the moves window ends here. */
  as_of?: string
  positions: PortfolioPosition[]
  totals: PortfolioTotals
  /** The funds' 30-day moves on the held symbols: the same rows as /moves, cut to the holdings, largest |net flow| first. */
  moves: { window_days: number; window_start: string; rows: MoveRow[] }
  /** Newest first; a symbol sold down to zero keeps its rows. */
  transactions: PortfolioTransaction[]
}

/** INCOMPLETE = not paid for (an unpaid checkout, Stripe's incomplete / paused): recorded, grants nothing. The admin's grant note never travels here. */
export type SubscriptionStatus = "TRIALING" | "ACTIVE" | "PAST_DUE" | "CANCELED" | "INCOMPLETE"
export const LIVE_STATUSES: readonly SubscriptionStatus[] = ["TRIALING", "ACTIVE", "PAST_DUE"]
export interface Subscription {
  id: number; plan: PlanCode; status: SubscriptionStatus; provider: "stripe" | "manual"
  current_period_end: string | null; cancel_at_period_end: boolean; created_at?: string; updated_at?: string
}
/**
 * A paid tier's recurring price as the provider states it; `amount` null while unknown (unconfigured, or Stripe
 * unreachable). `tax_behavior` is the Price's own word on VAT: inclusive / exclusive, or unspecified (nothing is claimed).
 */
export interface PlanPrice { id: string; amount: number | null; currency: string; interval: string | null; tax_behavior?: "inclusive" | "exclusive" | "unspecified" | null }
/** /billing/plans — the matrix with the prices; `configured` false = Stripe keys / price ids missing (checkout answers 503). */
export interface PlanInfo { code: PlanCode; price: PlanPrice | null; features: Features }
export interface BillingPlans { configured: boolean; currency: string; plans: PlanInfo[]; features?: string[]; provider?: string; plans_enforced?: boolean }
/** /billing/me — the effective plan, where it comes from, the subscription row behind it and the organisation seat if any. */
export interface BillingMe {
  plan: PlanCode
  source: PlanSource
  plan_source?: PlanSource
  own_plan?: PlanCode
  plan_until?: string | null
  features?: Features
  plans_enforced?: boolean
  subscription: Subscription | null
  org?: { id: number; name: string; plan: PlanCode; owner: boolean } | null
  configured?: boolean
  provider?: string
}

/** A seat: the invited address, the account behind it once accepted (`user_id`, `accepted_at`), `pending` until then. */
export interface OrgMember { id: number; role: "OWNER" | "MEMBER"; email: string; name: string | null; user_id: number | null; accepted_at: string | null; pending: boolean; invited_at?: string }
export interface Org { id: number; name: string; plan: PlanCode; seats: number; seats_used: number; owner_user_id: number; is_owner: boolean; created_at: string; members: OrgMember[] }

// Web dev: Vite proxies /api → :8000. Desktop/mobile bundles set VITE_API_BASE to the hosted API.
const BASE = `${import.meta.env.VITE_API_BASE ?? ""}/api/v1`

import { getToken } from "./auth"

function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { authorization: `Bearer ${t}` } : {}
}

/**
 * Every non-2xx answer. The message keeps its "<status> <body>" shape (pages match on `/^404\b/`), `status` and the
 * parsed `detail` are there for code that wants to branch without reading the text.
 */
export class ApiError extends Error {
  readonly status: number
  readonly detail: unknown
  constructor(status: number, detail: unknown, message: string) {
    super(message)
    this.name = "ApiError"
    this.status = status
    this.detail = detail
  }
}
/**
 * 402 {"detail": "plan_limit", feature, plan, limit, upgrade}: the account's plan does not include `feature`, or it hit
 * the plan's cap for it (`limit`). `upgrade` names the cheapest plan that lifts it. Thrown for the caller to render
 * inline (PlanGate's locked card) and announced on `instilens:plan-limit` for the global toast, so a page that does not
 * handle it still shows the reason and the way to /plan instead of a bare error.
 */
export class PlanLimitError extends ApiError {
  readonly feature: string
  readonly plan: PlanCode | string
  readonly limit: number | null
  readonly upgrade: PlanCode
  constructor(body: { detail?: unknown; feature?: unknown; plan?: unknown; limit?: unknown; upgrade?: unknown }) {
    super(402, body, "402 plan_limit")
    this.name = "PlanLimitError"
    this.feature = typeof body.feature === "string" ? body.feature : ""
    this.plan = typeof body.plan === "string" ? body.plan : "FREE"
    this.limit = typeof body.limit === "number" ? body.limit : null
    this.upgrade = body.upgrade === "PRO_PLUS" ? "PRO_PLUS" : "PRO"
  }
}
export const isPlanLimit = (e: unknown): e is PlanLimitError => e instanceof PlanLimitError
/** 503 from the billing routes: Stripe is not configured on this deployment — the UI shows "payments not open yet", never a checkout. */
export const isNotConfigured = (e: unknown): boolean => e instanceof ApiError && e.status === 503

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    window.dispatchEvent(new Event("instilens:unauthorized"))
    throw new Error("Oturum süresi doldu")
  }
  if (!res.ok) {
    const text = await res.text().catch(() => res.statusText)
    let body: Record<string, unknown> = {}
    try { const j: unknown = JSON.parse(text); if (j && typeof j === "object") body = j as Record<string, unknown> } catch { /* not JSON */ }
    if (res.status === 402) {
      // {"detail": "plan_limit", feature, plan, limit, upgrade} — or the same fields nested under `detail` (HTTPException(detail={...})).
      const nested = body.detail && typeof body.detail === "object" ? (body.detail as Record<string, unknown>) : null
      const fields = nested ?? body
      if (fields.detail === "plan_limit" || fields.feature !== undefined) {
        const err = new PlanLimitError(fields)
        window.dispatchEvent(new CustomEvent("instilens:plan-limit", { detail: err }))
        throw err
      }
    }
    throw new ApiError(res.status, "detail" in body ? body.detail : text, `${res.status} ${text}`)
  }
  if (res.status === 204) return undefined as T
  return res.json() as Promise<T>
}

async function get<T>(path: string, params: Record<string, unknown> = {}): Promise<T> {
  const qs = new URLSearchParams()
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === "") continue
    if (Array.isArray(v)) v.forEach((x) => qs.append(k, String(x)))
    else qs.set(k, String(v))
  }
  return handle<T>(await fetch(`${BASE}${path}${qs.size ? `?${qs}` : ""}`, { headers: authHeaders() }))
}

async function send<T>(method: "POST" | "DELETE" | "PATCH" | "PUT", path: string, body?: unknown): Promise<T> {
  return handle<T>(
    await fetch(`${BASE}${path}`, { method, headers: { "content-type": "application/json", ...authHeaders() }, body: body === undefined ? undefined : JSON.stringify(body) }),
  )
}

export const api = {
  radar: (market: Market, limit = 20, window: number | null = null) => get<Radar>("/radar", { market, limit, window }),
  timeline: (market: Market, symbol: string) => get<TimelineItem[]>(`/stocks/${symbol}/timeline`, { market }),
  compare: (a: string, b: string) => get<FundCompare>(`/funds/${a}/compare/${b}`),
  /** 2..6 fund codes of one market, sent comma-joined (`codes=A,B,C`). */
  fundOverlap: (codes: string[]) => get<FundOverlap>("/funds/overlap", { codes: codes.join(",") }),
  institutions: (market: Market) => get<InstitutionRow[]>("/institutions", { market }),
  institution: (market: Market, code: string) => get<InstitutionDetail>(`/institutions/${code}`, { market }),
  ttsStatus: () => get<{ provider: string | null }>("/tts/status"),
  /** 5-minute ticket for EventSource / <audio> — the only credentials allowed in a URL. */
  ticket: () => send<{ ticket: string; ttl_seconds: number }>("POST", "/auth/ticket"),
  noteAudioUrl: (id: number, gender: "female" | "male", ticket: string, voice?: string | null) => `${BASE}/ai-notes/${id}/audio?gender=${gender}&ticket=${encodeURIComponent(ticket)}${voice ? `&voice=${encodeURIComponent(voice)}` : ""}`,
  liveTv: () => get<{ name: string; channel_id: string; embed: string }[]>("/live-tv"),
  ttsPreviewUrl: (voiceId: string, lang: "tr" | "en", ticket: string) => `${BASE}/tts/preview/${encodeURIComponent(voiceId)}?lang=${lang}&ticket=${encodeURIComponent(ticket)}`,
  ttsVoices: (lang: "tr" | "en") => get<{ provider: string | null; lang: string; voices: TtsVoice[] }>("/tts/voices", { lang }),
  /** Public: whether the login page may offer self-registration. */
  authConfig: () => get<AuthConfig>("/auth/config"),
  verifyResend: () => send<{ ok: boolean; sent: boolean; email_verified: boolean }>("POST", "/auth/verify/resend"),
  mfaSetup: () => send<MfaSetup>("POST", "/auth/mfa/setup"),
  mfaEnable: (code: string) => send<{ ok: boolean }>("POST", "/auth/mfa/enable", { code }),
  mfaDisable: (code: string) => send<{ ok: boolean }>("POST", "/auth/mfa/disable", { code }),
  changePassword: (current_password: string, new_password: string) => send<{ access_token: string; token_type: string; user: import("./auth").User }>("POST", "/auth/password", { current_password, new_password }),
  logoutAll: () => send<{ ok: boolean }>("POST", "/auth/logout-all"),
  adminSettings: () => get<RuntimeSetting[]>("/admin/settings"),
  adminSaveSettings: (values: Record<string, unknown>) => send<{ changed: string[]; settings: RuntimeSetting[] }>("PUT", "/admin/settings", values),
  adminReleases: () => get<{ platforms: { key: string; label: string }[]; updater_pubkey_set: boolean; upload_key_set: boolean; releases: DesktopRelease[] }>("/admin/releases"),
  adminPatchRelease: (id: number, body: { status?: "DRAFT" | "PUBLISHED" | "WITHDRAWN"; notes?: string }) => send<DesktopRelease>("PATCH", `/admin/releases/${id}`, body),
  adminDeleteRelease: (id: number) => send<void>("DELETE", `/admin/releases/${id}`),
  desktopLatest: () => get<DesktopRelease | null>("/public/desktop/latest"),
  adminAudit: (limit = 100) => get<AuditEvent[]>("/admin/audit", { limit }),
  stockAi: (market: Market, symbol: string, lang: "tr" | "en" = "tr", refresh = false) => get<AiNote>(`/stocks/${symbol}/ai`, { market, lang, refresh: refresh || undefined }),
  brief: (market: Market, lang: "tr" | "en" = "tr", refresh = false) => get<AiNote | null>("/brief", { market, lang, refresh: refresh || undefined }),
  news: (market: Market, symbol?: string, limit = 40) => get<NewsItem[]>("/news", { market, symbol, limit }),
  freshness: (market: Market) => get<Freshness[]>("/freshness", { market }),
  signalPerformance: (market: Market) => get<SignalPerf>("/signals/performance", { market }),
  adminUsers: () => get<AdminUser[]>("/admin/users"),
  /** `plan_until` (ISO date | null) dates an admin-granted plan; sent only when the admin edits it. */
  adminPatchUser: (id: number, body: Partial<Pick<AdminUser, "plan" | "role" | "is_active" | "plan_until">>) => send<AdminUser>("PATCH", `/admin/users/${id}`, body),
  adminReview: () => get<Review>("/admin/review"),
  adminVerify: (body: { kind: "instrument" | "fund" | "institution"; id: number; name?: string }) => send<{ ok: boolean }>("POST", "/admin/review/verify", body),
  adminComputeOutcomes: () => send<{ updated: number }>("POST", "/admin/outcomes/compute"),
  adminNewsRules: () => get<NewsRule[]>("/admin/news/rules"),
  adminNewsRuleCreate: (body: Omit<NewsRule, "id">) => send<NewsRule>("POST", "/admin/news/rules", body),
  adminNewsRuleUpdate: (id: number, body: Omit<NewsRule, "id">) => send<NewsRule>("PUT", `/admin/news/rules/${id}`, body),
  adminNewsRuleDelete: (id: number) => send<void>("DELETE", `/admin/news/rules/${id}`),
  adminNewsSeed: () => send<{ created: number }>("POST", "/admin/news/rules/seed"),
  adminNewsReapply: () => send<{ TR: number; US: number }>("POST", "/admin/news/reapply"),
  adminNewsEnrich: (market: Market) => send<{ tagged: number }>("POST", `/admin/news/enrich?market=${market}`),
  adminConfig: () => get<AdminConfig>("/admin/config"),
  adminProviders: () => get<ProvidersStatus>("/admin/providers"),
  adminWaitlist: () => get<WaitlistRow[]>("/admin/waitlist"),
  adminPipelineRun: () => send<PipelineState & { started: boolean }>("POST", "/admin/pipeline/run"),
  adminPipelineStatus: () => get<PipelineState>("/admin/pipeline/status"),
  adminWarehouse: () => get<Warehouse>("/admin/warehouse"),
  /** Starts a build in a background thread; `started` false when one is already running (the pipeline's lock pattern), the lock status alongside either way. */
  adminWarehouseBuild: () => send<WarehouseStatus & { started: boolean }>("POST", "/admin/warehouse/build"),
  /** The file itself: a bearer-authenticated GET, so the caller saves the blob (an <a href> could not carry the header). */
  adminWarehouseDownload: async (b: WarehouseBuild): Promise<Blob> => {
    const res = await fetch(b.url, { headers: authHeaders() })
    if (!res.ok) await handle<never>(res)
    return res.blob()
  },
  stock: (market: Market, symbol: string) => get<StockDetail>(`/stocks/${symbol}`, { market }),
  fund: (code: string) => get<FundDetail>(`/funds/${code}`),
  events: (market: Market, limit = 50) => get<TxEvent[]>("/events", { market, limit }),
  search: (market: Market, q: string) => get<SearchHit[]>("/search", { market, q }),
  /** kinds null/empty = every kind; sent comma-joined (`kinds=news,note`). q is 2..128 chars, limit 1..50. */
  searchText: (market: Market, q: string, kinds: TextKind[] | null = null, limit = 20) => get<TextSearch>("/search/text", { market, q, kinds: kinds?.length ? kinds.join(",") : null, limit }),
  quotes: () => get<QuotesResponse>("/quotes"),
  screener: (market: Market, f: ScreenerFilters) => get<ScreenerRow[]>("/screener", { market, ...f }),
  /** window null = the market's own window; fund narrows the rows to that fund's own position changes (404 if unknown). */
  moves: (market: Market, kind: MoveKind, window: number | null = null, fund: string | null = null, limit = 25) => get<Moves>("/moves", { market, kind, window, fund, limit }),
  research: (question: string, market: Market) => send<ResearchAnswer>("POST", "/research", { question, market }),
  series: (market: Market, symbol: string) => get<StockSeries>(`/stocks/${symbol}/series`, { market }),
  /** lookback is a bar count (10..1000, the route bounds it); the API caps intraday ranges at what the provider keeps. */
  candles: (market: Market, symbol: string, interval: CandleInterval = "1d", lookback = 300) => get<Candles>(`/stocks/${symbol}/candles`, { market, interval, lookback }),
  fundamentals: (market: Market, symbol: string, period: FundamentalsPeriod = "annual") => get<Fundamentals>(`/stocks/${symbol}/fundamentals`, { market, period }),
  insiders: (market: Market, symbol: string, days: InsiderWindow = 90) => get<Insiders>(`/stocks/${symbol}/insiders`, { market, days }),
  /** form null = every form type. */
  filings: (market: Market, symbol: string, form: FilingForm | null = null, limit = 20) => get<Filings>(`/stocks/${symbol}/filings`, { market, form, limit }),
  ownership: (market: Market, symbol: string, limit = 50) => get<Ownership>(`/stocks/${symbol}/ownership`, { market, limit }),
  watchlist: () => get<WatchItem[]>("/watchlist"),
  addWatch: (body: { symbol?: string; fund_code?: string; market: Market }) => send<{ id: number; created: boolean }>("POST", "/watchlist", body),
  removeWatch: (id: number) => send<void>("DELETE", `/watchlist/${id}`),
  rules: () => get<{ rule_types: (RuleType | string)[]; rules: AlertRule[] }>("/alerts/rules"),
  addRule: (body: { symbol?: string; fund_code?: string; market: Market; rule_type: RuleType | string; params?: RuleParams }) => send<{ id: number }>("POST", "/alerts/rules", body),
  removeRule: (id: number) => send<void>("DELETE", `/alerts/rules/${id}`),
  notifications: () => get<Notification[]>("/alerts/notifications"),
  markRead: (id?: number) => send<{ marked: number }>("POST", `/alerts/notifications/read${id ? `?id=${id}` : ""}`),
  pushPublicKey: () => get<{ public_key: string | null; enabled: boolean; onesignal_app_id: string | null }>("/push/public-key"),
  pushSubscribe: (sub: PushSubscriptionJSON, user_agent: string) => send<{ id: number }>("POST", "/push/subscribe", { endpoint: sub.endpoint, keys: sub.keys, user_agent }),
  pushUnsubscribe: (endpoint: string) => send<void>("DELETE", `/push/subscribe?endpoint=${encodeURIComponent(endpoint)}`),
  pushTest: () => send<{ sent: number; onesignal: boolean; onesignal_error: string | null }>("POST", "/push/test"),
  mySettings: () => get<NotifySettings>("/me/settings"),
  saveSettings: (body: Partial<{ notify_email: boolean; notify_telegram_chat_id: string | null; notify_brief: boolean; brief_markets: Market[]; lang: "tr" | "en" }>) => send<{ ok: boolean }>("PUT", "/me/settings", body),
  testNotification: () => send<{ telegram: boolean | null; email: boolean | null }>("POST", "/me/settings/test"),
  evaluateAlerts: () => send<{ created: number }>("POST", "/alerts/evaluate"),
  eventStreamUrl: (market: Market, ticket: string, after?: number | null) => `${BASE}/events/stream?market=${market}&ticket=${encodeURIComponent(ticket)}${after != null ? `&after=${after}` : ""}`,
  // Portfolios (plan-gated: 402 plan_limit when the plan has none left / the position cap is reached).
  portfolios: () => get<Portfolio[]>("/portfolios"),
  createPortfolio: (body: { name: string; market: Market }) => send<Portfolio>("POST", "/portfolios", body),
  renamePortfolio: (id: number, name: string) => send<Portfolio>("PATCH", `/portfolios/${id}`, { name }),
  deletePortfolio: (id: number) => send<void>("DELETE", `/portfolios/${id}`),
  portfolio: (id: number) => get<PortfolioDetail>(`/portfolios/${id}`),
  /** Upsert by symbol (one row per portfolio × instrument); avg_cost null = quantity only, no P&L. Refused (409) for a derived row. */
  upsertPosition: (id: number, body: { symbol: string; quantity: number; avg_cost?: number | null; opened_at?: string | null; note?: string | null }) => send<PortfolioPosition>("PUT", `/portfolios/${id}/positions`, body),
  deletePosition: (id: number, positionId: number) => send<void>("DELETE", `/portfolios/${id}/positions/${positionId}`),
  addTransaction: (id: number, body: { symbol: string; side: "BUY" | "SELL"; quantity: number; price: number; traded_at: string; fee?: number | null; note?: string | null }) => send<PortfolioTransaction>("POST", `/portfolios/${id}/transactions`, body),
  deleteTransaction: (id: number, txId: number) => send<void>("DELETE", `/portfolios/${id}/transactions/${txId}`),
  // Billing (Stripe Checkout / Billing Portal; 503 "payments not configured" until the keys and price ids are set).
  billingPlans: () => get<BillingPlans>("/billing/plans"),
  /** 409 "subscription_exists" while a paid subscription is live: the change goes through the portal's subscription_update flow. */
  billingCheckout: (plan: PlanCode) => send<{ url: string }>("POST", "/billing/checkout", { plan }),
  /** Plain: the Billing Portal home. `subscription_update`: the portal opened on the plan picker of the live subscription. */
  billingPortal: (flow?: "subscription_update") => send<{ url: string }>("POST", "/billing/portal", flow ? { flow } : undefined),
  billingMe: () => get<BillingMe>("/billing/me"),
  // Organisation (PRO_PLUS seats): the caller's org, or null when they are in none.
  org: () => get<Org | null>("/org"),
  createOrg: (name: string) => send<Org>("POST", "/org", { name }),
  deleteOrg: () => send<void>("DELETE", "/org"),
  /** The seat is reserved either way; `sent` false = SMTP is not configured on this deployment, the link never left. */
  orgInvite: (email: string) => send<{ member_id: number; email: string; sent: boolean; org: Org }>("POST", "/org/invite", { email }),
  /** The mailed link's token; the signed-in account must be the invited (verified) address — 400 bad link, 409 with the reason otherwise. */
  orgAccept: (token: string) => send<Org>("POST", "/org/accept", { token }),
  /** The owner drops any seat; a member may pass their own row to leave. */
  orgRemoveMember: (memberId: number) => send<void>("DELETE", `/org/members/${memberId}`),
}
