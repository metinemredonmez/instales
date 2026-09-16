import { describe, expect, it } from "vitest"
import { translate } from "./i18n"
import { tr } from "@/i18n/tr"
import { en } from "@/i18n/en"

describe("translate", () => {
  it("returns the dictionary string for the language", () => {
    expect(translate("tr", "nav.radar")).toBe("Radar")
    expect(translate("en", "nav.logout")).toBe("Sign out")
    expect(translate("tr", "nav.logout")).toBe("Çıkış")
  })
  it("interpolates {vars}, including repeated placeholders and numbers", () => {
    expect(translate("tr", "common.more", { n: 3 })).toBe("+3 daha")
    expect(translate("en", "alerts.unread", { n: 12 })).toBe("12 unread")
    expect(translate("en", "timeline.period", { inc: 4, red: 1 })).toBe("4 funds increased · 1 reduced")
    expect(translate("en", "stock.activityWindow", { d: 100 })).toBe("100-day institutional activity")
  })
  it("leaves unknown placeholders untouched", () => {
    expect(translate("tr", "common.more", { x: 1 })).toBe("+{n} daha")
  })
  it("English covers every Turkish key with a non-empty string", () => {
    for (const k of Object.keys(tr) as (keyof typeof tr)[]) expect(en[k], k).toBeTruthy()
  })
  it("never uses buy/sell wording for disclosed moves", () => {
    for (const k of ["event.increase", "event.decrease", "ticker.buy", "ticker.sell", "event.increaseShort", "event.decreaseShort"] as const) {
      expect(tr[k].toLowerCase()).not.toMatch(/alım|satım/)
      expect(en[k].toLowerCase()).not.toMatch(/\b(buy|sell)\b/)
    }
  })
})
