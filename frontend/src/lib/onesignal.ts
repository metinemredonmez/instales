/** OneSignal web SDK loader. App id comes from the API so the bundle stays environment-free. */
declare global { interface Window { OneSignalDeferred?: ((os: OneSignalApi) => void | Promise<void>)[] } }
export interface OneSignalApi {
  init(opts: { appId: string; allowLocalhostAsSecureOrigin?: boolean }): Promise<void>
  login(externalId: string): Promise<void>
  logout(): Promise<void>
  Slidedown: { promptPush(): Promise<void> }
  Notifications: { permission: boolean; requestPermission(): Promise<void>; isPushSupported(): boolean }
  User: {
    /** Server-assigned id; undefined while the SDK only holds a local placeholder (user never created upstream). */
    onesignalId?: string
    externalId?: string
    PushSubscription: { id?: string; token?: string; optedIn?: boolean; optIn(): Promise<void>; optOut(): Promise<void> }
  }
}

/**
 * OneSignal must own the service-worker scope "/". A leftover registration of our VAPID worker (/sw.js, from a deploy
 * before OneSignal was configured) keeps the SDK from installing OneSignalSDKWorker.js, so the push token it obtains
 * is bound to the wrong worker and the upstream user is never created. Drop it (and its subscription) once.
 */
export async function evictForeignWorker(): Promise<boolean> {
  if (!("serviceWorker" in navigator)) return false
  let evicted = false
  for (const r of await navigator.serviceWorker.getRegistrations()) {
    const url = (r.active ?? r.waiting ?? r.installing)?.scriptURL ?? ""
    if (!url.endsWith("/sw.js")) continue
    try { await (await r.pushManager.getSubscription())?.unsubscribe() } catch { /* already gone */ }
    await r.unregister()
    evicted = true
  }
  return evicted
}

export const waitFor = async (cond: () => boolean, ms: number, step = 250) => {
  for (let t = 0; t < ms; t += step) { if (cond()) return true; await new Promise((r) => setTimeout(r, step)) }
  return cond()
}

/** Alias sent with OneSignal.login — prefixed because bare ids ("1") are blocked upstream; mirrors
 * onesignal_external_id() in backend/src/instilens/services/notify.py. */
export const oneSignalExternalId = (userId: number | string) => `instilens-${userId}`

let loaded = false
export const oneSignalLoaded = () => loaded
export function loadOneSignal(appId: string) {
  if (loaded) return
  loaded = true
  window.OneSignalDeferred = window.OneSignalDeferred || []
  window.OneSignalDeferred.push(async (os) => { await os.init({ appId, allowLocalhostAsSecureOrigin: true }) })
  const s = document.createElement("script")
  s.src = "https://cdn.onesignal.com/sdks/web/v16/OneSignalSDK.page.js"
  s.defer = true
  document.head.appendChild(s)
}
export function withOneSignal(fn: (os: OneSignalApi) => void | Promise<void>) {
  window.OneSignalDeferred = window.OneSignalDeferred || []
  window.OneSignalDeferred.push(fn)
}
/**
 * Run `fn` once the SDK is ready. `fallback` when the call itself throws; `notReady` (defaults to `fallback`)
 * when the SDK never initialises within `ms` — blocked CDN, CSP, or the OneSignal iframe failing to load.
 */
export function oneSignalCall<T>(fn: (os: OneSignalApi) => Promise<T>, fallback: T, ms = 8000, notReady: T = fallback): Promise<T> {
  return new Promise((resolve) => {
    const timer = window.setTimeout(() => resolve(notReady), ms)
    withOneSignal(async (os) => {
      window.clearTimeout(timer)
      try { resolve(await fn(os)) } catch { resolve(fallback) }
    })
  })
}
export {}
