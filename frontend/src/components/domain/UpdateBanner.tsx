import { useEffect, useState } from "react"
import { Download, X } from "lucide-react"
import { checkForUpdate, installUpdate, isDesktop, type UpdateInfo } from "@/lib/desktop"
import { useI18n } from "@/lib/i18n"
import { Button } from "@/components/ui/button"

/** Desktop only: checks once at launch and every 6 hours; shows a slim bar until installed or dismissed. */
export function UpdateBanner() {
  const { t } = useI18n()
  const [update, setUpdate] = useState<UpdateInfo | null>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const [hidden, setHidden] = useState(false)
  const [err, setErr] = useState<string | null>(null)
  useEffect(() => {
    if (!isDesktop()) return
    const run = () => checkForUpdate().then((u) => { if (u) { setUpdate(u); setHidden(false) } }).catch(() => {})
    run()
    const id = window.setInterval(run, 6 * 3600_000)
    return () => window.clearInterval(id)
  }, [])
  if (!update || hidden) return null
  return (
    <div className="rise flex flex-wrap items-center gap-3 border-b border-primary/40 bg-primary/10 px-4 py-2 text-sm">
      <Download className="size-4 text-primary" />
      <span><b>InstiLens {update.version}</b> {t("update.available")}</span>
      {update.notes && <span className="hidden truncate text-muted-foreground md:inline">— {update.notes.split("\n")[0]}</span>}
      <span className="ml-auto" />
      {progress !== null ? (
        <span className="inline-flex items-center gap-2 text-xs text-muted-foreground"><span className="h-1 w-32 overflow-hidden rounded-full bg-muted"><span className="bar-anim block h-full bg-primary" style={{ width: `${Math.round(progress * 100)}%` }} /></span>{Math.round(progress * 100)}%</span>
      ) : (
        <Button size="sm" onClick={() => { setErr(null); setProgress(0); installUpdate(setProgress).catch((e) => { setErr(String((e as Error).message ?? e)); setProgress(null) }) }}>{t("update.install")}</Button>
      )}
      {err && <span className="text-xs text-negative">{err}</span>}
      <button onClick={() => setHidden(true)} aria-label={t("common.close")} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
    </div>
  )
}
