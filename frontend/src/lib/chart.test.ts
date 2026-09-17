import { describe, expect, it, vi } from "vitest"
import type { Candle } from "./api"
import { closeChange, onOpenChart, openChart, sessionDate, zonedTime } from "./chart"

const bar = (t: number, c: number): Candle => ({ t, o: c, h: c, l: c, c, v: 1 })
const at = (iso: string) => Date.parse(iso) / 1000

describe("chart time helpers", () => {
  it("shifts an instant to the market's wall clock, per bar, so 14:35 UTC in Istanbul draws as 17:35", () => {
    const t = at("2026-09-17T14:35:00Z")
    expect(zonedTime(t, "Europe/Istanbul")).toBe(t + 3 * 3600)
    expect(zonedTime(t, "America/New_York")).toBe(t - 4 * 3600)   // EDT in September
    expect(zonedTime(at("2026-01-15T14:35:00Z"), "America/New_York")).toBe(at("2026-01-15T14:35:00Z") - 5 * 3600)   // EST in January
  })
  it("takes a midnight-UTC daily bar as its calendar date and any other instant as the date in the market's zone", () => {
    expect(sessionDate(at("2026-09-16T00:00:00Z"), "America/New_York")).toBe("2026-09-16")   // not "2026-09-15"
    expect(sessionDate(at("2026-09-16T04:00:00Z"), "America/New_York")).toBe("2026-09-16")   // yfinance's NY-midnight stamp
    expect(sessionDate(at("2026-09-16T22:30:00Z"), "Europe/Istanbul")).toBe("2026-09-17")     // 01:30 the next day in Istanbul
  })
})

describe("closeChange", () => {
  it("compares the last close with the previous bar on daily bars", () => {
    const r = closeChange([bar(at("2026-09-15T00:00:00Z"), 140), bar(at("2026-09-16T00:00:00Z"), 141.2)], "Europe/Istanbul")
    expect(r).toMatchObject({ close: 141.2, prev: 140 })
    expect(r!.abs).toBeCloseTo(1.2)
    expect(r!.pct).toBeCloseTo(0.857, 2)
  })
  it("compares with the previous session's last bar on intraday bars, not the bar before", () => {
    const bars = [bar(at("2026-09-16T13:00:00Z"), 100), bar(at("2026-09-16T14:55:00Z"), 102), bar(at("2026-09-17T07:00:00Z"), 103), bar(at("2026-09-17T07:05:00Z"), 101)]
    expect(closeChange(bars, "Europe/Istanbul")).toMatchObject({ close: 101, prev: 102, abs: -1 })
  })
  it("has no change when the range holds one session only, and nothing at all without bars", () => {
    expect(closeChange([bar(at("2026-09-17T07:00:00Z"), 103), bar(at("2026-09-17T07:05:00Z"), 101)], "Europe/Istanbul")).toEqual({ close: 101, prev: null, abs: null, pct: null })
    expect(closeChange([], "Europe/Istanbul")).toBeNull()
  })
})

describe("openChart bus", () => {
  it("delivers the upper-cased symbol and market to every subscriber, and nothing after unsubscribe", () => {
    const a = vi.fn(), b = vi.fn()
    const offA = onOpenChart(a)
    const offB = onOpenChart(b)
    openChart("asels", "TR")
    expect(a).toHaveBeenCalledWith({ symbol: "ASELS", market: "TR" })
    expect(b).toHaveBeenCalledWith({ symbol: "ASELS", market: "TR" })
    offA()
    openChart()
    expect(a).toHaveBeenCalledTimes(1)
    expect(b).toHaveBeenLastCalledWith({ symbol: null, market: null })
    offB()
  })
})
