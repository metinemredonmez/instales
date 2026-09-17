import { afterEach, describe, expect, it } from "vitest"
import { fmtCompact, fmtLots, fmtMoney, fmtNum, fmtPct, fmtPeriod, fmtPrice, setLocale } from "./format"

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

describe("fmtCompact", () => {
  afterEach(() => setLocale("tr"))

  it("uses Turkish scale words and a decimal comma, symbol first", () => {
    setLocale("tr")
    expect(fmtCompact(1_200_000_000, "TRY")).toBe("₺1,2 mr")
    expect(fmtCompact(345_000_000, "TRY")).toBe("₺345 mn")
    expect(fmtCompact(12_500, "TRY")).toBe("₺12,5 bin")
    expect(fmtCompact(2_300_000_000_000, "TRY")).toBe("₺2,3 tn")
  })
  it("steps up a unit instead of printing a four-digit mantissa", () => {
    expect(fmtCompact(999_950_000, "TRY")).toBe("₺1,0 mr")
    expect(fmtCompact(999_400_000, "TRY")).toBe("₺999 mn")
  })
  it("uses English scale words and a decimal point", () => {
    setLocale("en")
    expect(fmtCompact(1_200_000_000, "USD")).toBe("$1.2 bn")
    expect(fmtCompact(345_000_000, "USD")).toBe("$345 mn")
    expect(fmtCompact(12_500, "USD")).toBe("$12.5 k")
  })
  it("keeps negatives signed and leaves positives unsigned — these are levels, not flows", () => {
    expect(fmtCompact(-50_000_000, "TRY")).toBe("-₺50,0 mn")
    expect(fmtCompact(50_000_000, "TRY")).toBe("₺50,0 mn")
  })
  it("prints small values as-is: integers plain, fractions with two decimals", () => {
    expect(fmtCompact(950, "USD")).toBe("$950")
    expect(fmtCompact(45.2, "TRY")).toBe("₺45,20")
    expect(fmtCompact(0, "TRY")).toBe("₺0")
  })
  it("spells out a currency it has no symbol for and omits it for bare counts", () => {
    expect(fmtCompact(1_200_000_000, "CHF")).toBe("1,2 mr CHF")
    expect(fmtCompact(4_500_000_000)).toBe("4,5 mr")
    expect(fmtCompact(4_500_000_000, null)).toBe("4,5 mr")
  })
  it("renders missing or non-finite values as a dash, never zero", () => {
    expect(fmtCompact(null, "TRY")).toBe("—")
    expect(fmtCompact(undefined, "TRY")).toBe("—")
    expect(fmtCompact("abc", "TRY")).toBe("—")
    expect(fmtCompact(Number.NaN)).toBe("—")
  })
})

describe("fmtPrice", () => {
  afterEach(() => setLocale("tr"))

  it("prints a share price with two decimals and never abbreviates it", () => {
    setLocale("tr")
    expect(fmtPrice(1234.5, "TRY")).toBe("₺1.234,50")
    expect(fmtPrice(45.2, "TRY")).toBe("₺45,20")
    expect(fmtPrice(45, "TRY")).toBe("₺45,00")
    setLocale("en")
    expect(fmtPrice(5210.75, "USD")).toBe("$5,210.75")
    expect(fmtPrice(712000, "USD")).toBe("$712,000.00")
  })
  it("spells out an unknown currency, omits a missing one, and dashes a missing value", () => {
    expect(fmtPrice(12.5, "CHF")).toBe("12,50 CHF")
    expect(fmtPrice(12.5)).toBe("12,50")
    expect(fmtPrice(null, "TRY")).toBe("—")
    expect(fmtPrice(Number.NaN, "TRY")).toBe("—")
  })
})

describe("fmtNum", () => {
  afterEach(() => setLocale("tr"))

  it("formats in the UI locale with fixed decimals", () => {
    setLocale("tr")
    expect(fmtNum(12.34, 1)).toBe("12,3")
    expect(fmtNum(0.954, 2)).toBe("0,95")
    setLocale("en")
    expect(fmtNum(12.34, 1)).toBe("12.3")
    expect(fmtNum(1234.5, 1)).toBe("1,234.5")
  })
  it("signs positives only when asked and never signs zero", () => {
    expect(fmtNum(3.2, 1, true)).toBe("+3,2")
    expect(fmtNum(-3.2, 1, true)).toBe("-3,2")
    expect(fmtNum(0, 1, true)).toBe("0,0")
    expect(fmtNum(3.2, 1)).toBe("3,2")
  })
  it("renders null, undefined and NaN as a dash", () => {
    expect(fmtNum(null)).toBe("—")
    expect(fmtNum(undefined)).toBe("—")
    expect(fmtNum(Number.NaN)).toBe("—")
  })
})

describe("fmtPeriod", () => {
  it("prints a period end as MM/YYYY and leaves anything else untouched", () => {
    expect(fmtPeriod("2025-12-31")).toBe("12/2025")
    expect(fmtPeriod("2025-03-31")).toBe("03/2025")
    expect(fmtPeriod("2025")).toBe("2025")
  })
})
