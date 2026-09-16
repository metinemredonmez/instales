import { api } from "./api"

function b64ToUint8(b64: string) {
  const pad = "=".repeat((4 - (b64.length % 4)) % 4)
  const raw = atob((b64 + pad).replace(/-/g, "+").replace(/_/g, "/"))
  return Uint8Array.from(raw, (c) => c.charCodeAt(0))
}

export const pushSupported = () => "serviceWorker" in navigator && "PushManager" in window && window.isSecureContext

export async function registerSw() {
  if (!("serviceWorker" in navigator)) return null
  return navigator.serviceWorker.register("/sw.js")
}

/** Ask permission, subscribe this device, and store the subscription on the server. */
export async function enablePush(): Promise<"ok" | "denied" | "unsupported" | "disabled"> {
  if (!pushSupported()) return "unsupported"
  const { public_key, enabled } = await api.pushPublicKey()
  if (!enabled || !public_key) return "disabled"
  const reg = (await registerSw()) ?? (await navigator.serviceWorker.ready)
  const perm = await Notification.requestPermission()
  if (perm !== "granted") return "denied"
  const sub = (await reg.pushManager.getSubscription()) ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: b64ToUint8(public_key) }))
  await api.pushSubscribe(sub.toJSON(), navigator.userAgent.slice(0, 200))
  return "ok"
}

export async function disablePush() {
  const reg = await navigator.serviceWorker.getRegistration()
  const sub = await reg?.pushManager.getSubscription()
  if (sub) { await api.pushUnsubscribe(sub.endpoint); await sub.unsubscribe() }
}

export async function pushState(): Promise<"on" | "off"> {
  if (!pushSupported()) return "off"
  const reg = await navigator.serviceWorker.getRegistration()
  return (await reg?.pushManager.getSubscription()) ? "on" : "off"
}
