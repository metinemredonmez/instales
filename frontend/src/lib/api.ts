/** Typed client for the InstiLens API. Shapes mirror backend/src/instilens/services/analytics.py. */

export type Market = "TR" | "US"
export type Confidence = "EXACT" | "GROUPED" | "INFERRED"
export type Activity = "NEW" | "ADD" | "REDUCE" | "EXIT" | "HOLD"
export type SignalType =
  | "ACCUMULATION"
  | "DISTRIBUTION"
  | "POSITIVE_DIVERGENCE"
  | "NEGATIVE_DIVERGENCE"
  | "NEW_POSITION_CLUSTER"
  | "EXIT_CLUSTER"

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
  overlap_pct: number
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
export interface AdminUser { id: number; email: string; name: string; plan: string; role: string; is_active: boolean; created_at: string; last_login_at: string | null }
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

export interface NotifySettings { email: string; notify_email: boolean; notify_telegram_chat_id: string | null; notify_brief: boolean; lang: "tr" | "en"; channels: { telegram: boolean; email: boolean } }

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
  scores: Partial<Record<"SMART_MONEY" | "CONSENSUS", ScoreDetail>>
  latest_period_end: string | null
  top_buyers: PositionChange[]
  top_sellers: PositionChange[]
  signals: Signal[]
  events: TxEvent[]
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

export interface WaitlistRow { id: number; email: string; name: string | null; lang: string; source: string | null; created_at: string }

export interface AdminConfig {
  environment: string; public_url: string; allow_registration: boolean; database: string
  kap_adapter: string; kap_api_base_url: string | null; sec_adapter: string; sec_ciks: string[]
  ai: { provider: string; model: string; configured: boolean; news_enrich: boolean }
  tts: { provider: string | null; voices: Record<string, boolean> }
  news: { enabled: boolean; newsapi: boolean }
  channels: { telegram: boolean; email: boolean; web_push: boolean; onesignal: boolean }
}

export interface SearchHit {
  kind: "stock" | "fund" | "institution"
  key: string
  label: string
  name: string
  href: string
}

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

export interface ResearchAnswer {
  question: string
  answer: string
  model: string
  usage: Record<string, number>
  tool_calls: { name: string; input: Record<string, unknown>; output_preview: string }[]
}

export interface StockSeries {
  symbol: string
  prices: { date: string; close: number }[]
  holdings: { date: string; quantity: number; funds: number }[]
}

export interface WatchItem {
  id: number
  kind: "stock" | "fund"
  ref: string
  name: string
  institution?: string
  smart_money_score?: number | null
  funds_increasing?: number | null
  funds_reducing?: number | null
  net_flow_value?: number
}

export interface AlertRule {
  id: number
  rule_type: string
  params: Record<string, unknown>
  is_active: boolean
  symbol: string | null
  fund_code: string | null
}

export interface Notification {
  id: number
  title: string
  body: string
  link: string | null
  created_at: string
  read_at: string | null
}

// Web dev: Vite proxies /api → :8000. Desktop/mobile bundles set VITE_API_BASE to the hosted API.
const BASE = `${import.meta.env.VITE_API_BASE ?? ""}/api/v1`

import { getToken } from "./auth"

function authHeaders(): Record<string, string> {
  const t = getToken()
  return t ? { authorization: `Bearer ${t}` } : {}
}

async function handle<T>(res: Response): Promise<T> {
  if (res.status === 401) {
    window.dispatchEvent(new Event("instilens:unauthorized"))
    throw new Error("Oturum süresi doldu")
  }
  if (!res.ok) throw new Error(`${res.status} ${await res.text().catch(() => res.statusText)}`)
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
  institutions: (market: Market) => get<InstitutionRow[]>("/institutions", { market }),
  institution: (market: Market, code: string) => get<InstitutionDetail>(`/institutions/${code}`, { market }),
  ttsStatus: () => get<{ provider: string | null }>("/tts/status"),
  noteAudioUrl: (id: number, gender: "female" | "male" = "female") => `${BASE}/ai-notes/${id}/audio?gender=${gender}&token=${encodeURIComponent(getToken() ?? "")}`,
  stockAi: (market: Market, symbol: string, lang: "tr" | "en" = "tr", refresh = false) => get<AiNote>(`/stocks/${symbol}/ai`, { market, lang, refresh: refresh || undefined }),
  brief: (market: Market, lang: "tr" | "en" = "tr", refresh = false) => get<AiNote | null>("/brief", { market, lang, refresh: refresh || undefined }),
  news: (market: Market, symbol?: string, limit = 40) => get<NewsItem[]>("/news", { market, symbol, limit }),
  freshness: (market: Market) => get<Freshness[]>("/freshness", { market }),
  signalPerformance: (market: Market) => get<SignalPerf>("/signals/performance", { market }),
  adminUsers: () => get<AdminUser[]>("/admin/users"),
  adminPatchUser: (id: number, body: Partial<Pick<AdminUser, "plan" | "role" | "is_active">>) => send<AdminUser>("PATCH", `/admin/users/${id}`, body),
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
  adminWaitlist: () => get<WaitlistRow[]>("/admin/waitlist"),
  adminPipelineRun: () => send<PipelineState & { started: boolean }>("POST", "/admin/pipeline/run"),
  adminPipelineStatus: () => get<PipelineState>("/admin/pipeline/status"),
  stock: (market: Market, symbol: string) => get<StockDetail>(`/stocks/${symbol}`, { market }),
  fund: (code: string) => get<FundDetail>(`/funds/${code}`),
  events: (market: Market, limit = 50) => get<TxEvent[]>("/events", { market, limit }),
  search: (market: Market, q: string) => get<SearchHit[]>("/search", { market, q }),
  screener: (market: Market, f: ScreenerFilters) => get<ScreenerRow[]>("/screener", { market, ...f }),
  research: (question: string, market: Market) => send<ResearchAnswer>("POST", "/research", { question, market }),
  series: (market: Market, symbol: string) => get<StockSeries>(`/stocks/${symbol}/series`, { market }),
  watchlist: () => get<WatchItem[]>("/watchlist"),
  addWatch: (body: { symbol?: string; fund_code?: string; market: Market }) => send<{ id: number; created: boolean }>("POST", "/watchlist", body),
  removeWatch: (id: number) => send<void>("DELETE", `/watchlist/${id}`),
  rules: () => get<{ rule_types: string[]; rules: AlertRule[] }>("/alerts/rules"),
  addRule: (body: { symbol?: string; fund_code?: string; market: Market; rule_type: string; params?: Record<string, unknown> }) => send<{ id: number }>("POST", "/alerts/rules", body),
  removeRule: (id: number) => send<void>("DELETE", `/alerts/rules/${id}`),
  notifications: () => get<Notification[]>("/alerts/notifications"),
  markRead: (id?: number) => send<{ marked: number }>("POST", `/alerts/notifications/read${id ? `?id=${id}` : ""}`),
  pushPublicKey: () => get<{ public_key: string | null; enabled: boolean; onesignal_app_id: string | null }>("/push/public-key"),
  pushSubscribe: (sub: PushSubscriptionJSON, user_agent: string) => send<{ id: number }>("POST", "/push/subscribe", { endpoint: sub.endpoint, keys: sub.keys, user_agent }),
  pushUnsubscribe: (endpoint: string) => send<void>("DELETE", `/push/subscribe?endpoint=${encodeURIComponent(endpoint)}`),
  pushTest: () => send<{ sent: number; onesignal: boolean }>("POST", "/push/test"),
  mySettings: () => get<NotifySettings>("/me/settings"),
  saveSettings: (body: Partial<{ notify_email: boolean; notify_telegram_chat_id: string | null; notify_brief: boolean; lang: "tr" | "en" }>) => send<{ ok: boolean }>("PUT", "/me/settings", body),
  testNotification: () => send<{ telegram: boolean | null; email: boolean | null }>("POST", "/me/settings/test"),
  evaluateAlerts: () => send<{ created: number }>("POST", "/alerts/evaluate"),
  eventStreamUrl: (market: Market) => `${BASE}/events/stream?market=${market}&token=${encodeURIComponent(getToken() ?? "")}`,
}
