import { api } from "./api"
import type { Key } from "@/i18n/tr"
import { evictForeignWorker, loadOneSignal, oneSignalCall, oneSignalExternalId, waitFor } from "./onesignal"

function b64ToUint8(b64: string) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4)
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}

export const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && window.isSecureContext

/**
 * One delivery path per deployment: when the server has a OneSignal app id, OneSignal owns the prompt, the
 * subscription and the service worker; otherwise our own VAPID subscription + /sw.js. Never both.
 */
export type PushMode = "onesignal" | "vapid" | "none"
export async function pushMode(): Promise<{ mode: PushMode; appId: string | null; publicKey: string | null }> {
  const k = await api.pushPublicKey()
  if (k.onesignal_app_id) return { mode: "onesignal", appId: k.onesignal_app_id, publicKey: null }
  if (k.enabled && k.public_key) return { mode: "vapid", appId: null, publicKey: k.public_key }
  return { mode: "none", appId: null, publicKey: null }
}

export async function registerSw() {
  if (!("serviceWorker" in navigator)) return null
  return navigator.serviceWorker.register("/sw.js")
}

/** Ask permission and subscribe this device (OneSignal or VAPID, per the server's configuration). */
export type PushEnableResult = "ok" | "denied" | "unsupported" | "disabled" | "sdk" | "nouser"

export async function enablePush(userId?: number | null): Promise<PushEnableResult> {
  if (!pushSupported()) return "unsupported"
  const { mode, appId, publicKey } = await pushMode()
  if (mode === "onesignal" && appId) {
    loadOneSignal(appId)
    return oneSignalCall(async (os) => {
      await evictForeignWorker()
      if (userId) await os.login(oneSignalExternalId(userId))
      await os.Notifications.requestPermission()
      if (!os.Notifications.permission) return "denied" as const
      await os.User.PushSubscription.optIn()
      // Delivery targets external_id upstream, so the user must exist there — a local-only id means createUser never
      // landed. One logout/login cycle re-sends identity + subscription before we give up.
      if (await waitFor(() => !!os.User.onesignalId, 6000)) return "ok" as const
      await os.logout()
      if (userId) await os.login(oneSignalExternalId(userId))
      return (await waitFor(() => !!os.User.onesignalId, 8000)) ? ("ok" as const) : ("nouser" as const)
    }, "disabled", 8000, "sdk")
  }
  if (mode !== "vapid" || !publicKey) return "disabled"
  const reg = (await registerSw()) ?? (await navigator.serviceWorker.ready)
  const perm = await Notification.requestPermission()
  if (perm !== "granted") return "denied"
  const sub = (await reg.pushManager.getSubscription()) ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToUint8(publicKey) }))
  await api.pushSubscribe(sub.toJSON(), navigator.userAgent.slice(0, 200))
  return "ok"
}

export async function disablePush() {
  const { mode, appId } = await pushMode()
  if (mode === "onesignal" && appId) {
    loadOneSignal(appId)
    await oneSignalCall(async (os) => { await os.User.PushSubscription.optOut() }, undefined)
    return
  }
  const reg = await navigator.serviceWorker.getRegistration()
  const sub = await reg?.pushManager.getSubscription()
  if (sub) { await api.pushUnsubscribe(sub.endpoint); await sub.unsubscribe() }
}

export async function pushState(): Promise<"on" | "off"> {
  if (!pushSupported()) return "off"
  const { mode, appId } = await pushMode().catch(() => ({ mode: "none" as PushMode, appId: null }))
  if (mode === "onesignal" && appId) {
    loadOneSignal(appId)
    // "on" only when the device can actually be reached: permission, an opted-in subscription and an upstream user.
    return oneSignalCall(async (os) => (os.Notifications.permission && os.User.PushSubscription.optedIn === true && !!os.User.PushSubscription.id && !!os.User.onesignalId ? "on" : "off") as "on" | "off", "off")
  }
  if (mode !== "vapid") return "off"
  const reg = await navigator.serviceWorker.getRegistration()
  return (await reg?.pushManager.getSubscription()) ? "on" : "off"
}

/** i18n key for an enablePush outcome — shared by the settings card and the login-time prompt. */
export const pushResultKey = (r: PushEnableResult): Key =>
  r === "ok" ? "notify.push.on" : r === "denied" ? "notify.push.denied" : r === "unsupported" ? "notify.push.unsupported" : r === "sdk" ? "notify.push.sdk" : r === "nouser" ? "notify.push.noUser" : "notify.push.noKey"

// ---------------------------------------------------------------- login-time prompt
// Push is per device, so the "asked already" record lives in this browser; a dismissal snoozes for a week, and
// after three dismissals the prompt stays quiet (Settings still has the switch).
export const PUSH_PROMPT_KEY = "il.pushPrompt"
export const PUSH_PROMPT_SNOOZE_MS = 7 * 86_400_000
export const PUSH_PROMPT_MAX_DISMISSALS = 3
export type PushPromptRecord = { dismissedAt: number; count: number }

export function shouldPromptPush(i: { supported: boolean; mode: PushMode; state: "on" | "off"; permission: NotificationPermission; record: PushPromptRecord | null; now: number }): boolean {
  if (!i.supported || i.mode === "none" || i.state === "on" || i.permission === "denied") return false
  if (!i.record) return true
  if (i.record.count >= PUSH_PROMPT_MAX_DISMISSALS) return false
  return i.now - i.record.dismissedAt >= PUSH_PROMPT_SNOOZE_MS
}

export function readPushPromptRecord(): PushPromptRecord | null {
  try {
    const raw = localStorage.getItem(PUSH_PROMPT_KEY)
    const r = raw ? (JSON.parse(raw) as Partial<PushPromptRecord>) : null
    return r && typeof r.dismissedAt === "number" ? { dismissedAt: r.dismissedAt, count: r.count ?? 1 } : null
  } catch { return null }
}

export function dismissPushPrompt(now = Date.now()) {
  const r = readPushPromptRecord()
  try { localStorage.setItem(PUSH_PROMPT_KEY, JSON.stringify({ dismissedAt: now, count: (r?.count ?? 0) + 1 })) } catch { /* private mode */ }
}

/** True when the signed-in user should be offered push on this device right now. */
export async function pushPromptDue(): Promise<boolean> {
  if (!pushSupported()) return false
  const { mode } = await pushMode().catch(() => ({ mode: "none" as PushMode }))
  if (mode === "none") return false
  const state = await pushState().catch(() => "off" as const)
  return shouldPromptPush({ supported: true, mode, state, permission: Notification.permission, record: readPushPromptRecord(), now: Date.now() })
}
