/** OneSignal web SDK loader. App id comes from the API so the bundle stays environment-free. */
declare global { interface Window { OneSignalDeferred?: ((os: OneSignalApi) => void | Promise<void>)[] } }
export interface OneSignalApi {
  init(opts: { appId: string; allowLocalhostAsSecureOrigin?: boolean }): Promise<void>
  login(externalId: string): Promise<void>
  logout(): Promise<void>
  Slidedown: { promptPush(): Promise<void> }
  Notifications: { permission: boolean; requestPermission(): Promise<void>; isPushSupported(): boolean }
  User: { PushSubscription: { optedIn?: boolean; optIn(): Promise<void>; optOut(): Promise<void> } }
}

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
