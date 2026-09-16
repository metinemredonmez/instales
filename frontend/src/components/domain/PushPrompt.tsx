import { useEffect, useState } from "react"
import { BellRing, X } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { dismissPushPrompt, enablePush, pushPromptDue, pushResultKey } from "@/lib/push"
import { Button } from "@/components/ui/button"

/**
 * Offers push on this device shortly after sign-in when it is supported, configured and not yet on. Dismissing
 * snoozes it for a week (three times at most); enabling goes through the same path as the switch in Settings.
 */
export function PushPrompt() {
  const { user } = useAuth()
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState<{ text: string; ok: boolean } | null>(null)
  useEffect(() => {
    if (!user) { setOpen(false); return }
    let alive = true
    // Let the dashboard paint first; the question lands once the person has seen what they signed in to.
    const timer = window.setTimeout(() => { pushPromptDue().then((due) => { if (alive) setOpen(due) }).catch(() => {}) }, 2500)
    return () => { alive = false; window.clearTimeout(timer) }
  }, [user])
  if (!user || !open) return null
  const later = () => { dismissPushPrompt(); setOpen(false) }
  const enable = async () => {
    setBusy(true)
    const r = await enablePush(user.id).catch(() => "disabled" as const)
    setBusy(false)
    setMsg({ text: t(pushResultKey(r)), ok: r === "ok" })
    if (r === "ok") window.setTimeout(() => setOpen(false), 2500)
    else if (r === "denied" || r === "unsupported") dismissPushPrompt()
  }
  return (
    <div className="rise flex flex-wrap items-center gap-3 border-b border-primary/40 bg-primary/10 px-4 py-2 text-sm" role="status">
      <BellRing className="size-4 text-primary" />
      <span><b>{t("push.prompt.title")}</b> <span className="text-muted-foreground">{t("push.prompt.body")}</span></span>
      <span className="ml-auto" />
      {msg && <span className={msg.ok ? "text-xs text-positive" : "text-xs text-negative"}>{msg.text}</span>}
      {!msg?.ok && <Button size="sm" onClick={enable} disabled={busy}>{busy ? "…" : t("push.prompt.enable")}</Button>}
      {!msg?.ok && <Button size="sm" variant="ghost" onClick={later} disabled={busy}>{t("push.prompt.later")}</Button>}
      <button onClick={later} aria-label={t("common.close")} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
    </div>
  )
}
