/** Tauri-only helpers. Every call is a no-op in the browser so the same bundle serves web and desktop. */
export const isDesktop = () => typeof window !== "undefined" && "__TAURI_INTERNALS__" in window

export type UpdateInfo = { version: string; notes: string; date: string | null }

/** Ask the server for a newer signed build. Resolves null when up to date or not running in Tauri. */
export async function checkForUpdate(): Promise<UpdateInfo | null> {
  if (!isDesktop()) return null
  const { check } = await import("@tauri-apps/plugin-updater")
  const u = await check()
  return u ? { version: u.version, notes: u.body ?? "", date: u.date ?? null } : null
}

/** Download + install the pending update, then relaunch. `onProgress` gets 0..1. */
export async function installUpdate(onProgress?: (p: number) => void): Promise<void> {
  const { check } = await import("@tauri-apps/plugin-updater")
  const { relaunch } = await import("@tauri-apps/plugin-process")
  const u = await check()
  if (!u) return
  let total = 0, got = 0
  await u.downloadAndInstall((ev) => {
    if (ev.event === "Started") total = ev.data.contentLength ?? 0
    else if (ev.event === "Progress") { got += ev.data.chunkLength; if (total) onProgress?.(got / total) }
    else if (ev.event === "Finished") onProgress?.(1)
  })
  await relaunch()
}

export async function currentVersion(): Promise<string | null> {
  if (!isDesktop()) return null
  const { getVersion } = await import("@tauri-apps/api/app")
  return getVersion()
}
