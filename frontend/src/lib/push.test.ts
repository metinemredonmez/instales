import { afterEach, describe, expect, it, vi } from "vitest"

const pushPublicKey = vi.fn()
const pushSubscribe = vi.fn()
vi.mock("./api", async (orig) => ({ ...(await orig<typeof import("./api")>()), api: { pushPublicKey: () => pushPublicKey(), pushSubscribe: (...a: unknown[]) => pushSubscribe(...a) } }))

import { PlanLimitError } from "./api"
import { PUSH_PROMPT_SNOOZE_MS, enablePush, pushResultKey, shouldPromptPush } from "./push"

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
    for (const r of ["ok", "denied", "unsupported", "disabled", "sdk", "nouser", "plan"] as const) expect(pushResultKey(r)).toMatch(/^notify\.push\./)
    expect(pushResultKey("ok")).toBe("notify.push.on")
    expect(pushResultKey("nouser")).toBe("notify.push.noUser")
    expect(pushResultKey("plan")).toBe("notify.push.plan")
  })
})

describe("enablePush and the plan", () => {
  /** jsdom has no push machinery: a VAPID-shaped server, a service worker whose subscribe records the call, a granted permission. */
  const unsubscribe = vi.fn(async () => true)
  const subscribe = vi.fn(async () => ({ toJSON: () => ({ endpoint: "https://push.example/1", keys: { p256dh: "k", auth: "a" } }), unsubscribe }))
  const stub = () => {
    vi.stubGlobal("PushManager", class {})
    vi.stubGlobal("Notification", { permission: "default", requestPermission: async () => "granted" })
    Object.defineProperty(window, "isSecureContext", { value: true, configurable: true })
    Object.defineProperty(navigator, "serviceWorker", { value: { register: async () => ({ pushManager: { getSubscription: async () => null, subscribe } }) }, configurable: true })
    pushPublicKey.mockResolvedValue({ public_key: "AAAA", enabled: true, onesignal_app_id: null })
  }
  afterEach(() => { vi.unstubAllGlobals(); subscribe.mockClear(); unsubscribe.mockClear(); pushSubscribe.mockReset(); pushPublicKey.mockReset() })

  it("a plan without push stops before any prompt or subscription", async () => {
    stub()
    expect(await enablePush(7, false)).toBe("plan")
    expect(pushPublicKey).not.toHaveBeenCalled()
    expect(subscribe).not.toHaveBeenCalled()
  })

  it("a 402 on the registration undoes the browser subscription, so the switch does not read 'on'", async () => {
    stub()
    pushSubscribe.mockRejectedValue(new PlanLimitError({ detail: "plan_limit", feature: "push", plan: "FREE", limit: 0, upgrade: "PRO" }))
    expect(await enablePush(7)).toBe("plan")
    expect(subscribe).toHaveBeenCalledTimes(1)
    expect(unsubscribe).toHaveBeenCalledTimes(1)
    pushSubscribe.mockResolvedValue({})
    expect(await enablePush(7)).toBe("ok")
    expect(unsubscribe).toHaveBeenCalledTimes(1)
  })
})
