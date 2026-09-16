/** OneSignal web SDK loader. App id comes from the API so the bundle stays environment-free. */
declare global { interface Window { OneSignalDeferred?: ((os: OneSignalApi) => void | Promise<void>)[] } }
interface OneSignalApi {
  init(opts: { appId: string; allowLocalhostAsSecureOrigin?: boolean }): Promise<void>
  login(externalId: string): Promise<void>
  logout(): Promise<void>
  Slidedown: { promptPush(): Promise<void> }
  Notifications: { permission: boolean; requestPermission(): Promise<void>; isPushSupported(): boolean }
}

let loaded = false
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
export {}
