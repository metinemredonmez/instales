import { describe, expect, it } from "vitest"
import { fmtCountdown, fmtInZone, fmtOpensAt, fmtQuoteChange, fmtQuotePrice, fmtSessionDate, marketStateDetail, marketStateLabel, minutesUntil, quoteTone, trTimeSuffix } from "./quotes"
import { translate, type T } from "./i18n"
import { setLocale } from "./format"
import type { MarketStatus } from "./api"

const tTr: T = (k, v) => translate("tr", k, v)
const tEn: T = (k, v) => translate("en", k, v)

describe("quote formatting", () => {
  it("prints FX with four decimals and indices with none, per the decimals the API declares", () => {
    setLocale("en")
    expect(fmtQuotePrice(41.2345678, 4)).toBe("41.2346")
    expect(fmtQuotePrice(41.2, 4)).toBe("41.2000")            // padded, never truncated to fewer digits
    expect(fmtQuotePrice(10482.6, 0)).toBe("10,483")
    expect(fmtQuotePrice(6512.24, 0)).toBe("6,512")
  })

  it("follows the UI locale for separators", () => {
    setLocale("tr")
    expect(fmtQuotePrice(10482.6, 0)).toBe("10.483")
    expect(fmtQuotePrice(41.2345678, 4)).toBe("41,2346")
    setLocale("en")
  })

  it("signs the day change, uses the locale's decimal separator like the price, and shows a dash when the source gave none", () => {
    setLocale("en")
    expect(fmtQuoteChange(0.4567)).toBe("+0.46%")
    expect(fmtQuoteChange(-1.2)).toBe("-1.20%")
    expect(fmtQuoteChange(0)).toBe("0.00%")
    expect(fmtQuoteChange(null)).toBe("—")
    expect(fmtQuoteChange(Number.NaN)).toBe("—")
    setLocale("tr")
    expect(fmtQuoteChange(0.4567)).toBe("+0,46%")
    expect(fmtQuoteChange(-1.2)).toBe("-1,20%")
    setLocale("en")
  })

  it("maps the change to a colour tone", () => {
    expect(quoteTone(0.01)).toBe("pos")
    expect(quoteTone(-0.01)).toBe("neg")
    expect(quoteTone(0)).toBe("flat")
    expect(quoteTone(null)).toBe("flat")
  })

  it("prints the session date from its parts, so a Friday bar stays Friday in every zone", () => {
    setLocale("en")
    expect(fmtSessionDate("2026-09-11")).toBe("Fri 11 Sept")
    setLocale("tr")
    expect(fmtSessionDate("2026-09-11")).toBe("11 Eyl Cum")
    setLocale("en")
    expect(fmtSessionDate("2026-9-1")).toBe("—")
    expect(fmtSessionDate("")).toBe("—")
  })
})

describe("market-status countdown", () => {
  const now = Date.parse("2026-09-16T13:48:00+03:00")   // Wednesday 13:48 Istanbul

  it("counts whole minutes, rounding up, and never goes negative", () => {
    expect(minutesUntil("2026-09-16T15:00:00+03:00", now)).toBe(72)
    expect(minutesUntil("2026-09-16T13:48:30+03:00", now)).toBe(1)
    expect(minutesUntil("2026-09-16T13:00:00+03:00", now)).toBe(0)
    expect(minutesUntil("not a date", now)).toBeNaN()
  })

  it("shows a dash, not a zero, for a moment that does not parse", () => {
    expect(fmtCountdown("not a date", now, tTr)).toBe("—")
    expect(fmtCountdown("not a date", now, tEn)).toBe("—")
  })

  it("picks the Turkish locative suffix from the last spoken word of the time", () => {
    expect(trTimeSuffix("10:00")).toBe("da")     // onda
    expect(trTimeSuffix("18:00")).toBe("de")     // on sekizde
    expect(trTimeSuffix("04:00")).toBe("te")     // dörtte
    expect(trTimeSuffix("09:30")).toBe("da")     // dokuz otuzda
    expect(trTimeSuffix("16:00")).toBe("da")     // on altıda
    expect(trTimeSuffix("20:00")).toBe("de")     // yirmide
    expect(trTimeSuffix("Per 10:00")).toBe("da")
  })

  it("spells out the market zone in the tooltip moment", () => {
    expect(fmtInZone("2026-09-17T07:00:00Z", "Europe/Istanbul", "en")).toBe("Thu 10:00 GMT+3")
    expect(fmtInZone("2026-09-17T07:00:00Z", "Europe/Istanbul", "tr")).toBe("Per 10:00 GMT+3")
    expect(fmtInZone("2026-09-16T13:30:00Z", "America/New_York", "en")).toBe("Wed 09:30 GMT-4")
    expect(fmtInZone("not a date", "America/New_York", "en")).toBe("—")
  })

  it("renders midnight as 00:00, not 24:00", () => {
    expect(fmtOpensAt("2026-09-16T21:00:00Z", "Europe/Istanbul", now, "en")).toBe("Thu 00:00")
    expect(fmtInZone("2026-09-16T21:00:00Z", "Europe/Istanbul", "en")).toBe("Thu 00:00 GMT+3")
  })

  it("formats hours + minutes in the UI language", () => {
    expect(fmtCountdown("2026-09-16T15:00:00+03:00", now, tTr)).toBe("1s 12dk")
    expect(fmtCountdown("2026-09-16T15:00:00+03:00", now, tEn)).toBe("1h 12m")
    expect(fmtCountdown("2026-09-16T14:20:00+03:00", now, tTr)).toBe("32dk")
    expect(fmtCountdown("2026-09-16T18:00:00+03:00", now, tEn)).toBe("4h 12m")
  })

  it("shows the opening time in the market's zone, with the weekday when it is not today there", () => {
    expect(fmtOpensAt("2026-09-17T07:00:00Z", "Europe/Istanbul", now, "tr")).toBe("Per 10:00")   // tomorrow 10:00 Istanbul
    expect(fmtOpensAt("2026-09-16T13:30:00Z", "America/New_York", now, "en")).toBe("09:30")     // same day in New York
    expect(fmtOpensAt("2026-09-21T07:00:00Z", "Europe/Istanbul", now, "en")).toBe("Mon 10:00")
  })

  it("builds the pill text per state", () => {
    const open: MarketStatus = { state: "open", next_change_at: "2026-09-16T15:00:00Z", tz: "Europe/Istanbul" }  // 18:00 Istanbul
    expect(marketStateLabel("TR", "open", tTr)).toBe("BIST açık")
    expect(marketStateDetail(open, now, tTr, "tr")).toBe("kapanışa 4s 12dk")

    const closed: MarketStatus = { state: "closed", next_change_at: "2026-09-17T07:00:00Z", tz: "Europe/Istanbul" }
    expect(marketStateLabel("TR", "closed", tTr)).toBe("Kapalı")
    expect(marketStateDetail(closed, now, tTr, "tr")).toBe("Per 10:00'da açılır")
    expect(marketStateDetail(closed, now, tEn, "en")).toBe("opens Thu 10:00")
    const closedUs: MarketStatus = { state: "closed", next_change_at: "2026-09-17T08:00:00Z", tz: "America/New_York" }  // pre-market 04:00 NY
    expect(marketStateDetail(closedUs, now, tTr, "tr")).toBe("Per 04:00'te açılır")

    const pre: MarketStatus = { state: "pre", next_change_at: "2026-09-16T13:30:00Z", tz: "America/New_York" }
    const at0900ny = Date.parse("2026-09-16T13:00:00Z")
    expect(marketStateLabel("US", "pre", tEn)).toBe("Pre-market")
    expect(marketStateDetail(pre, at0900ny, tEn, "en")).toBe("opens in 30m")

    const post: MarketStatus = { state: "post", next_change_at: "2026-09-17T00:00:00Z", tz: "America/New_York" }  // 20:00 NY
    const at1815ny = Date.parse("2026-09-16T22:15:00Z")
    expect(marketStateLabel("US", "post", tTr)).toBe("After-hours")
    expect(marketStateDetail(post, at1815ny, tTr, "tr")).toBe("kapanışa 1s 45dk")
    expect(marketStateLabel("US", "open", tEn)).toBe("Open")
  })
})
