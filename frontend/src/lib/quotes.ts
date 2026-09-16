import { useQuery } from "@tanstack/react-query"
import { api, type Market, type MarketState, type MarketStatus } from "@/lib/api"
import { locale } from "@/lib/format"
import type { T } from "@/lib/i18n"

/**
 * Header quotes + market clocks. The numbers come from /quotes (Yahoo via the backend, 60 s server cache); nothing
 * here invents a value — a quote the API omitted is simply not shown, and the countdown is arithmetic on the
 * `next_change_at` the server sent.
 */
export const useQuotes = () =>
  useQuery({ queryKey: ["quotes"], queryFn: api.quotes, refetchInterval: 60_000, staleTime: 55_000, placeholderData: (prev) => prev })

/** Price with exactly the decimals the API declares for that key (4 for FX, 0 for indices), in the UI locale. */
export function fmtQuotePrice(price: number, decimals: number): string {
  return new Intl.NumberFormat(locale(), { minimumFractionDigits: decimals, maximumFractionDigits: decimals }).format(price)
}

/** Day change as a signed percentage with two decimals in the UI locale (same separator as the price); "—" when the source gave none. */
export function fmtQuoteChange(pct: number | null | undefined): string {
  if (pct === null || pct === undefined || !Number.isFinite(pct)) return "—"
  const n = new Intl.NumberFormat(locale(), { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(pct)
  return `${pct > 0 ? "+" : ""}${n}%`
}

export type QuoteTone = "pos" | "neg" | "flat"
export function quoteTone(pct: number | null | undefined): QuoteTone {
  if (pct === null || pct === undefined || !Number.isFinite(pct) || pct === 0) return "flat"
  return pct > 0 ? "pos" : "neg"
}

/**
 * Whole minutes until `at` (never negative; a moment that already passed is 0). Rounded up so 59.5 min reads
 * "1s 0dk", not "59dk". NaN when `at` does not parse — the caller shows a dash, never a made-up zero.
 */
export function minutesUntil(at: string | Date, now: number | Date): number {
  const target = typeof at === "string" ? Date.parse(at) : at.getTime()
  const from = typeof now === "number" ? now : now.getTime()
  if (!Number.isFinite(target)) return Number.NaN
  return Math.max(0, Math.ceil((target - from) / 60_000))
}

/** "1s 12dk" / "1h 12m" (hours only when there is at least one); minutes alone below an hour; "—" for an unparsable moment. */
export function fmtCountdown(at: string | Date, now: number | Date, t: T): string {
  const mins = minutesUntil(at, now)
  if (Number.isNaN(mins)) return "—"
  const h = Math.floor(mins / 60)
  const m = mins % 60
  return h > 0 ? t("countdown.hm", { h, m }) : t("countdown.m", { m })
}

/** Wall-clock time of `at` in the market's own zone ("10:00"); prefixed with the weekday when it is not today there. */
export function fmtOpensAt(at: string, tz: string, now: number | Date, lang: "tr" | "en"): string {
  const d = new Date(at)
  if (Number.isNaN(d.getTime())) return "—"
  const loc = lang === "en" ? "en-GB" : "tr-TR"
  const time = new Intl.DateTimeFormat(loc, { timeZone: tz, hour: "2-digit", minute: "2-digit", hour12: false }).format(d)
  const day = new Intl.DateTimeFormat("en-CA", { timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit" })
  const sameDay = day.format(d) === day.format(typeof now === "number" ? new Date(now) : now)
  if (sameDay) return time
  return `${new Intl.DateTimeFormat(loc, { timeZone: tz, weekday: "short" }).format(d)} ${time}`
}

/**
 * Turkish locative suffix for a clock time, decided by the last spoken word: 10:00 "on" → 'da, 18:00 "on sekiz" → 'de,
 * 04:00 "dört" → 'te, 09:30 "otuz" → 'da. Minutes win when they are not zero.
 */
const TR_TIME_SUFFIX: Record<number, string> = { 0: "da", 1: "de", 2: "de", 3: "te", 4: "te", 5: "te", 6: "da", 7: "de", 8: "de", 9: "da", 10: "da", 20: "de", 30: "da", 40: "ta", 50: "de" }
export function trTimeSuffix(time: string): string {
  const m = /(\d{1,2}):(\d{2})/.exec(time)
  if (!m) return "da"
  const h = Number(m[1])
  const min = Number(m[2])
  const n = min === 0 ? h : min
  return TR_TIME_SUFFIX[n % 10 === 0 ? n : n % 10] ?? "da"
}

/**
 * The state a market enters when `next_change_at` passes — shown until /quotes answers with the new schedule, so the
 * pill never keeps calling a session "open" after its close. BIST: open ↔ closed; NYSE: closed → pre → open → post → closed.
 */
export function nextMarketState(market: Market, state: MarketState): MarketState {
  if (market === "TR") return state === "open" ? "closed" : "open"
  return state === "closed" ? "pre" : state === "pre" ? "open" : state === "open" ? "post" : "closed"
}

/** State label for the pill: "BIST açık" for the Turkish session, plain "Açık"/"Pre-market"/"After-hours"/"Kapalı" otherwise. */
export function marketStateLabel(market: Market, state: MarketState, t: T): string {
  if (state === "open") return t(market === "TR" ? "market.state.openBist" : "market.state.open")
  return t(state === "pre" ? "market.state.pre" : state === "post" ? "market.state.post" : "market.state.closed")
}

/** The second half of the pill: a countdown while a session (or pre/post window) runs, the opening time while closed. */
export function marketStateDetail(status: MarketStatus, now: number | Date, t: T, lang: "tr" | "en"): string {
  if (status.state === "closed") {
    const at = fmtOpensAt(status.next_change_at, status.tz, now, lang)
    return t("market.opensAt", { at, suf: trTimeSuffix(at) })
  }
  const d = fmtCountdown(status.next_change_at, now, t)
  return status.state === "pre" ? t("market.toOpen", { d }) : t("market.toClose", { d })
}
