const CURRENCY: Record<string, string> = { TR: "₺", US: "$" }

export function fmtMoney(v: number | string | null | undefined, market = "TR"): string {
  if (v === null || v === undefined) return "—"
  const n = typeof v === "string" ? Number(v) : v
  const sign = n < 0 ? "-" : n > 0 ? "+" : ""
  const a = Math.abs(n)
  const [num, unit] =
    a >= 1e9 ? [a / 1e9, "B"] : a >= 1e6 ? [a / 1e6, "M"] : a >= 1e3 ? [a / 1e3, "K"] : [a, ""]
  return `${sign}${CURRENCY[market] ?? ""}${num.toFixed(num >= 100 || unit === "" ? 0 : 1)}${unit}`
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
  return new Intl.NumberFormat("tr-TR").format(v)
}

export function fmtPct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined) return "—"
  return `${v > 0 ? "+" : ""}${v.toFixed(digits)}%`
}

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "—"
  return new Date(iso).toLocaleDateString("tr-TR", { day: "2-digit", month: "short", year: "numeric" })
}

export function fmtDateTime(iso: string): string {
  return new Date(iso).toLocaleString("tr-TR", { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" })
}
