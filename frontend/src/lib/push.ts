import { api } from "./api"
import { loadOneSignal, oneSignalCall } from "./onesignal"

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
export async function enablePush(userId?: number | null): Promise<"ok" | "denied" | "unsupported" | "disabled"> {
  if (!pushSupported()) return "unsupported"
  const { mode, appId, publicKey } = await pushMode()
  if (mode === "onesignal" && appId) {
    loadOneSignal(appId)
    return oneSignalCall(async (os) => {
      if (userId) await os.login(String(userId))
      await os.Notifications.requestPermission()
      if (!os.Notifications.permission) return "denied" as const
      await os.User.PushSubscription.optIn()
      return "ok" as const
    }, "disabled")
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
    return oneSignalCall(async (os) => (os.Notifications.permission && os.User.PushSubscription.optedIn !== false ? "on" : "off") as "on" | "off", "off")
  }
  if (mode !== "vapid") return "off"
  const reg = await navigator.serviceWorker.getRegistration()
  return (await reg?.pushManager.getSubscription()) ? "on" : "off"
}
