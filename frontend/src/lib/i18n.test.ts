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
  it("labels every runtime setting the admin form can edit, in both languages", () => {
    // Mirrors the EDITABLE dict in backend/src/instilens/services/runtime_settings.py — keep in step when a key is added there.
    const EDITABLE = [
      "allow_registration", "plans_enforced", "account_lockout_attempts", "account_lockout_minutes", "auth_rate_limit_per_minute",
      "ai_requests_per_hour", "ai_model", "ai_news_enabled", "ai_news_model", "ai_extract_model",
      "elevenlabs_model", "elevenlabs_speed", "elevenlabs_paragraph_pause_s", "elevenlabs_stability", "elevenlabs_style", "elevenlabs_speaker_boost", "elevenlabs_extra_voices",
      "breached_password_check", "news_enabled", "live_tv_channels",
      "kap_adapter", "kap_public_days_back", "kap_public_max_details", "kap_public_max_reports", "kap_public_fund_codes",
      "kap_insiders_enabled", "kap_insiders_max_details", "sec_ciks", "market_holidays_tr", "price_provider", "quotes_interval_s",
      "fundamentals_enabled", "fundamentals_max_instruments", "sec_form4_enabled", "sec_form4_max_issuers", "warehouse_enabled",
    ]
    for (const k of EDITABLE) {
      for (const dict of [tr, en] as Record<string, string>[]) {
        expect(dict[`settings.k.${k}`], `settings.k.${k}`).toBeTruthy()
        expect(dict[`settings.d.${k}`], `settings.d.${k}`).toBeTruthy()
      }
    }
  })
  it("never uses buy/sell wording for disclosed moves", () => {
    for (const k of ["event.increase", "event.decrease", "ticker.buy", "ticker.sell", "event.increaseShort", "event.decreaseShort"] as const) {
      expect(tr[k].toLowerCase()).not.toMatch(/alım|satım/)
      expect(en[k].toLowerCase()).not.toMatch(/\b(buy|sell)\b/)
    }
  })
})
