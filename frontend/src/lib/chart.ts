import { useEffect, useState } from "react"
import type { Candle, CandleInterval, Market, PriceSource } from "./api"

/**
 * The floating chart window (components/domain/ChartWidget). Like the live bus in lib/live: any row anywhere calls
 * `openChart(symbol, market)` and the one widget instance AppShell mounts picks it up — no prop drilling through the
 * pages. Two subscribers listen: the shell's open flag (useChartOpen) and the widget itself (symbol, un-minimise).
 */
export type ChartRequest = { symbol: string | null; market: Market | null }
export type ChartListener = (r: ChartRequest) => void

const listeners = new Set<ChartListener>()

/** Open the widget on `symbol` of `market`; with no symbol it just opens (or un-minimises) on whatever it last showed. */
export function openChart(symbol?: string, market?: Market) {
  const r: ChartRequest = { symbol: symbol ? symbol.toUpperCase() : null, market: market ?? null }
  listeners.forEach((fn) => fn(r))
}

export function onOpenChart(fn: ChartListener) {
  listeners.add(fn)
  return () => { listeners.delete(fn) }
}

/** The shell's open flag: starts closed on every load, flips on the sidebar toggle, and any `openChart()` call turns it on. */
export function useChartOpen() {
  const [open, setOpen] = useState(false)
  useEffect(() => onOpenChart(() => setOpen(true)), [])
  return { open, setOpen, toggle: () => setOpen((o) => !o) }
}

// What the widget remembers between loads: where it sits, how big it is, the last symbol/interval, and whether it was left minimised.
const STORE_KEY = "instilens.chart"
export type ChartSaved = { x: number; y: number; w: number; h: number; symbol: string | null; market: Market | null; interval: CandleInterval; min: boolean }
export const INTERVALS: readonly CandleInterval[] = ["5m", "15m", "1h", "1d"] as const
const isInterval = (v: unknown): v is CandleInterval => typeof v === "string" && (INTERVALS as readonly string[]).includes(v)

export function loadChart(): Partial<ChartSaved> | null {
  try {
    const s = JSON.parse(localStorage.getItem(STORE_KEY) || "null") as Partial<ChartSaved> | null
    if (!s || typeof s !== "object") return null
    // A remembered interval the API no longer knows falls back to daily rather than a 422 on the first fetch.
    return { ...s, interval: isInterval(s.interval) ? s.interval : undefined }
  } catch { return null }
}
export function saveChart(s: ChartSaved) { try { localStorage.setItem(STORE_KEY, JSON.stringify(s)) } catch { /* ignore */ } }

/** The provider a candle answer names, as the header quote type spells it, so quoteSourceLabel() prints the same line as the strip. */
export const candleSource = (source: PriceSource, delayed: boolean) => [{ source, delayed }]

// One formatter per zone: formatToParts runs once per bar (≤ 1000) on every answer, so the constructor is the cost worth caching.
const partsFmt = new Map<string, Intl.DateTimeFormat>()
function zoneParts(tz: string): Intl.DateTimeFormat {
  let f = partsFmt.get(tz)
  if (!f) { f = new Intl.DateTimeFormat("en-US", { timeZone: tz, hourCycle: "h23", year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" }); partsFmt.set(tz, f) }
  return f
}
/** The wall clock of instant `t` (epoch seconds) in `tz`, as numbers; the zone's own DST rules through Intl. */
function wallClock(t: number, tz: string): { y: number; m: number; d: number; hh: number; mm: number; ss: number } {
  const p: Record<string, number> = {}
  for (const x of zoneParts(tz).formatToParts(new Date(t * 1000))) if (x.type !== "literal") p[x.type] = Number(x.value)
  return { y: p.year, m: p.month, d: p.day, hh: p.hour === 24 ? 0 : p.hour, mm: p.minute, ss: p.second }
}

/**
 * The bar's instant shifted so the chart library, which draws UTC timestamps as UTC, shows the market's own wall
 * clock: 14:35 in Istanbul reads 14:35 on the axis, whatever the viewer's zone. Per bar, so a range crossing a DST
 * change keeps every bar on its own local time.
 */
export function zonedTime(t: number, tz: string): number {
  const w = wallClock(t, tz)
  return Date.UTC(w.y, w.m - 1, w.d, w.hh, w.mm, w.ss) / 1000
}

const pad = (n: number) => String(n).padStart(2, "0")
/**
 * The session date a bar belongs to, "YYYY-MM-DD". A daily bar stamped at midnight UTC is a calendar date, not an
 * instant (a New York date would otherwise read as the evening before), so it is taken as the UTC date; anything
 * else is the wall-clock date in the market's zone.
 */
export function sessionDate(t: number, tz: string): string {
  if (t % 86_400 === 0) { const d = new Date(t * 1000); return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}` }
  const w = wallClock(t, tz)
  return `${w.y}-${pad(w.m)}-${pad(w.d)}`
}

/**
 * Last close against the previous session's close — the figure the strip and the stock page call "change". On daily
 * bars that is the bar before; on intraday bars the last bar of the previous session date (not the bar five minutes
 * ago), so the number means the same thing on every interval. null when the range holds no earlier session.
 */
export function closeChange(bars: Candle[], tz: string): { close: number; prev: number | null; abs: number | null; pct: number | null } | null {
  const last = bars.at(-1)
  if (!last) return null
  const today = sessionDate(last.t, tz)
  let prev: Candle | undefined
  for (let i = bars.length - 2; i >= 0; i--) { if (sessionDate(bars[i].t, tz) !== today) { prev = bars[i]; break } }
  if (!prev || prev.c === 0) return { close: last.c, prev: null, abs: null, pct: null }
  return { close: last.c, prev: prev.c, abs: last.c - prev.c, pct: ((last.c / prev.c) - 1) * 100 }
}

export type Rgba = [number, number, number, number]
let probe: CanvasRenderingContext2D | null | undefined
/**
 * Any colour the browser can paint — the theme's oklch tokens included — as [r, g, b, a] (0–255, 0–1), read back
 * from a 1×1 canvas: the chart library parses hex/rgb/hsl itself and takes this as its parser for the rest. null
 * where there is no 2D canvas (jsdom) or the string is not a colour; the caller then keeps the string as it is.
 */
export function cssToRgba(color: string): Rgba | null {
  if (probe === undefined) {
    try { probe = typeof document === "undefined" ? null : document.createElement("canvas").getContext("2d", { willReadFrequently: true }) ?? null } catch { probe = null }
  }
  if (!probe) return null
  try {
    probe.fillStyle = "#010203"   // sentinel: an unparsable string leaves the previous value in place
    probe.fillStyle = color
    if (probe.fillStyle === "#010203" && !/^#010203$/i.test(color.trim())) return null
    probe.clearRect(0, 0, 1, 1)
    probe.fillRect(0, 0, 1, 1)
    const [r, g, b, a] = probe.getImageData(0, 0, 1, 1).data
    return [r, g, b, a / 255]
  } catch { return null }
}

/** The theme token's current value ("oklch(…)" from index.css, light or dark as the root class says) — the raw string, which the chart parses through cssToRgba; `fallback` where no stylesheet answers. */
export function themeColor(token: string, fallback = ""): string {
  if (typeof document === "undefined") return fallback
  return getComputedStyle(document.documentElement).getPropertyValue(token).trim() || fallback
}
/** The same token at `alpha` as an rgba() string the chart parses natively; the opaque token when the canvas cannot resolve it. */
export function themeColorAlpha(token: string, alpha: number, fallback = ""): string {
  const c = themeColor(token, fallback)
  const rgba = cssToRgba(c)
  return rgba ? `rgba(${rgba[0]}, ${rgba[1]}, ${rgba[2]}, ${alpha})` : c
}
