import { describe, expect, it } from "vitest"
import { PUSH_PROMPT_SNOOZE_MS, pushResultKey, shouldPromptPush } from "./push"

const base = { supported: true, mode: "onesignal" as const, state: "off" as const, permission: "default" as NotificationPermission, record: null, now: 1_000_000_000_000 }

describe("login-time push prompt", () => {
  it("asks a signed-in person once push is possible and not yet on", () => {
    expect(shouldPromptPush(base)).toBe(true)
  })
  it("stays quiet when unsupported, unconfigured, already on or blocked by the browser", () => {
    expect(shouldPromptPush({ ...base, supported: false })).toBe(false)
    expect(shouldPromptPush({ ...base, mode: "none" })).toBe(false)
    expect(shouldPromptPush({ ...base, state: "on" })).toBe(false)
    expect(shouldPromptPush({ ...base, permission: "denied" })).toBe(false)
  })
  it("snoozes a dismissal for a week and gives up after three", () => {
    const d = { dismissedAt: base.now - 1000, count: 1 }
    expect(shouldPromptPush({ ...base, record: d })).toBe(false)
    expect(shouldPromptPush({ ...base, record: d, now: base.now + PUSH_PROMPT_SNOOZE_MS })).toBe(true)
    expect(shouldPromptPush({ ...base, record: { dismissedAt: 0, count: 3 } })).toBe(false)
  })
  it("maps every enable outcome to a message key", () => {
    for (const r of ["ok", "denied", "unsupported", "disabled", "sdk", "nouser"] as const) expect(pushResultKey(r)).toMatch(/^notify\.push\./)
    expect(pushResultKey("ok")).toBe("notify.push.on")
    expect(pushResultKey("nouser")).toBe("notify.push.noUser")
  })
})
