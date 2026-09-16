import { describe, expect, it } from "vitest"
import { fmtLots, fmtMoney, fmtPct } from "./format"

describe("fmtMoney", () => {
  it("abbreviates with the market's currency and an explicit sign", () => {
    expect(fmtMoney(1_250_000)).toBe("+₺1.3M")
    expect(fmtMoney(-42_000_000_000, "US")).toBe("-$42.0B")
    expect(fmtMoney(950, "US")).toBe("+$950")
    expect(fmtMoney("12500", "TR")).toBe("+₺12.5K")
  })
  it("drops the decimal once the mantissa reaches 100", () => {
    expect(fmtMoney(150_000)).toBe("+₺150K")
    expect(fmtMoney(99_900)).toBe("+₺99.9K")
  })
  it("handles zero, null and unknown markets", () => {
    expect(fmtMoney(0)).toBe("₺0")
    expect(fmtMoney(null)).toBe("—")
    expect(fmtMoney(undefined)).toBe("—")
    expect(fmtMoney(1000, "XX")).toBe("+1.0K")
  })
})

describe("fmtLots", () => {
  it("keeps integers below a thousand as-is, signed", () => {
    expect(fmtLots(7)).toBe("+7")
    expect(fmtLots(-7)).toBe("-7")
    expect(fmtLots(0)).toBe("0")
  })
  it("abbreviates K/M and coarsens precision for large values", () => {
    expect(fmtLots(1_500)).toBe("+1.5K")
    expect(fmtLots(-250_000)).toBe("-250K")
    expect(fmtLots(2_400_000)).toBe("+2.4M")
    expect(fmtLots(120_000_000)).toBe("+120M")
  })
  it("renders missing values as a dash", () => {
    expect(fmtLots(null)).toBe("—")
    expect(fmtLots(undefined)).toBe("—")
  })
})

describe("fmtPct", () => {
  it("signs positives and keeps one decimal by default", () => {
    expect(fmtPct(3.456)).toBe("+3.5%")
    expect(fmtPct(-0.04)).toBe("-0.0%")
    expect(fmtPct(null)).toBe("—")
  })
})
