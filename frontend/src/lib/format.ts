const CURRENCY: Record<string, string> = { TR: "₺", US: "$" }

// Locale for dates and numbers; set by LangProvider (tr-TR / en-GB). Pages remount on language change.
let LOCALE = "tr-TR"
export function setLocale(lang: "tr" | "en") {
  LOCALE = lang === "en" ? "en-GB" : "tr-TR"
}
export function locale() {
  return LOCALE
}

export function fmtMoney(v: number | string | null | undefined, market = "TR"): string {
  if (v === null || v === undefined) return "—"
  const n = typeof v === "string" ? Number(v) : v
  const sign = n < 0 ? "-" : n > 0 ? "+" : ""
  const a = Math.abs(n)
  const [num, unit] =
    a >= 1e9 ? [a / 1e9, "B"] : a >= 1e6 ? [a / 1e6, "M"] : a >= 1e3 ? [a / 1e3, "K"] : [a, ""]
  return `${sign}${CURRENCY[market] ?? ""}${num.toFixed(num >= 100 || unit === "" ? 0 : 1)}${unit}`
}

// Statement currencies as the UI writes them; anything else is spelled out after the amount ("1,2 mr CHF").
const CURRENCY_SYMBOL: Record<string, string> = { TRY: "₺", USD: "$", EUR: "€", GBP: "£" }
// Scale words the financial press uses in each language: bin/mn/mr in Turkish, k/mn/bn in English.
const SCALE: Record<string, [string, string, string, string]> = { "tr-TR": ["bin", "mn", "mr", "tn"], "en-GB": ["k", "mn", "bn", "tn"] }

/** Plain number in the UI locale (decimal comma in Turkish) with fixed decimals; `signed` prefixes "+" like fmtPct does. */
export function fmtNum(v: number | null | undefined, digits = 1, signed = false): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—"
  const n = new Intl.NumberFormat(LOCALE, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(v)
  return signed && v > 0 ? `+${n}` : n
}

/**
 * Compact reported amount for fundamentals — `₺1,2 mr` / `$1.2 bn`, `345 mn` — as opposed to fmtMoney, which renders
 * flows (signed both ways, B/M/K, the market's currency). These are levels in the statement's own currency: negatives
 * keep their sign, positives do not get one, and the mantissa follows the UI locale. No currency → a bare count
 * (shares outstanding). Below a thousand the value is printed as is, so an EPS of 12.3 stays 12.3.
 */
export function fmtCompact(v: number | string | null | undefined, currency?: string | null): string {
  if (v === null || v === undefined) return "—"
  const n = typeof v === "string" ? Number(v) : v
  if (!Number.isFinite(n)) return "—"
  const a = Math.abs(n)
  const scale = SCALE[LOCALE] ?? SCALE["en-GB"]
  let tier = a >= 1e12 ? 4 : a >= 1e9 ? 3 : a >= 1e6 ? 2 : a >= 1e3 ? 1 : 0
  let num = a / 1000 ** tier
  // 999.95 mn would print as "1.000 mn"; once the rounded mantissa reaches a thousand, step up to the next word.
  if (tier > 0 && tier < 4 && Math.round(num) >= 1000) { tier += 1; num = a / 1000 ** tier }
  const unit = tier === 0 ? "" : scale[tier - 1]
  const digits = unit === "" ? (Number.isInteger(a) ? 0 : 2) : num >= 100 ? 0 : 1
  const body = `${fmtNum(num, digits)}${unit ? ` ${unit}` : ""}`
  const sym = currency ? CURRENCY_SYMBOL[currency] : undefined
  const sign = n < 0 ? "-" : ""
  if (sym) return `${sign}${sym}${body}`
  return currency ? `${sign}${body} ${currency}` : `${sign}${body}`
}

/**
 * A per-share price — the 52-week range — with two decimals and the currency symbol, never abbreviated: a 1.234,50 TRY
 * share is "₺1.234,50", not "₺1,2 bin". Same symbol convention as fmtCompact (an unknown currency is spelled out after).
 */
export function fmtPrice(v: number | null | undefined, currency?: string | null): string {
  if (v === null || v === undefined || !Number.isFinite(v)) return "—"
  const sym = currency ? CURRENCY_SYMBOL[currency] : undefined
  const sign = v < 0 ? "-" : ""
  const body = fmtNum(Math.abs(v), 2)
  if (sym) return `${sign}${sym}${body}`
  return currency ? `${sign}${body} ${currency}` : `${sign}${body}`
}

export function fmtLots(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—"
  const sign = v < 0 ? "-" : v > 0 ? "+" : ""
  const a = Math.abs(v)
  if (a >= 1e6) return `${sign}${(a / 1e6).toFixed(a >= 1e8 ? 0 : 1)}M`
  if (a >= 1e3) return `${sign}${(a / 1e3).toFixed(a >= 1e5 ? 0 : 1)}K`
  return `${sign}${a}`
}

export function fmtQty(v: number): string {
  return new Intl.NumberFormat(LOCALE).format(v)
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—"
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString(LOCALE, { day: "2-digit", month: "short", year: "numeric" })
}

/** Statement column head: the period end as MM/YYYY — annual and quarterly columns both read as a period, the day adds nothing. */
export function fmtPeriod(iso: string): string {
  const [y, m] = iso.split("-")
  return y && m ? `${m}/${y}` : iso
}

export function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString(LOCALE, { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
}
