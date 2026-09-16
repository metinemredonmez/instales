import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { disablePush, enablePush, pushState, pushSupported } from "@/lib/push"
import { withOneSignal } from "@/lib/onesignal"
import { useI18n } from "@/lib/i18n"

/** Where alerts and the morning brief get delivered. Telegram is free and instant; e-mail needs SMTP on the server. */
export function NotifySettingsCard() {
  const { t } = useI18n()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ["me-settings"], queryFn: api.mySettings })
  const [chat, setChat] = useState("")
  const [email, setEmail] = useState(false)
  const [brief, setBrief] = useState(true)
  useEffect(() => { if (q.data) { setChat(q.data.notify_telegram_chat_id ?? ""); setEmail(q.data.notify_email); setBrief(q.data.notify_brief) } }, [q.data])
  const save = useMutation({ mutationFn: () => api.saveSettings({ notify_email: email, notify_telegram_chat_id: chat || null, notify_brief: brief }), onSuccess: () => qc.invalidateQueries({ queryKey: ["me-settings"] }) })
  const test = useMutation({ mutationFn: api.testNotification })
  const ch = q.data?.channels
  const [push, setPush] = useState<"on" | "off" | "…">("…")
  const [pushMsg, setPushMsg] = useState("")
  useEffect(() => { pushState().then(setPush) }, [])
  const togglePush = async () => {
    if (push === "on") { await disablePush(); setPush("off"); return }
    withOneSignal((os) => os.Slidedown.promptPush())  // OneSignal prompt when configured; harmless otherwise
    const r = await enablePush()
    setPush(r === "ok" ? "on" : "off")
    setPushMsg(r === "ok" ? t("notify.push.on") : r === "denied" ? t("notify.push.denied") : r === "unsupported" ? t("notify.push.unsupported") : t("notify.push.noKey"))
  }
  const pushTest = useMutation({ mutationFn: api.pushTest })
  return (
    <Section title={t("notify.title")} hint={t("notify.hint")}>
      <div className="space-y-3 p-4 text-sm">
        <div className="rounded-md border border-border bg-card p-3">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="font-medium">📱 {t("notify.push.title")}</div>
              <div className="text-[11px] text-muted-foreground">{t("notify.push.howto")} {!pushSupported() && t("notify.push.noHttps")}</div>
            </div>
            <Button size="sm" variant={push === "on" ? "outline" : "default"} onClick={togglePush} disabled={push === "…"}>{push === "on" ? t("common.off") : t("common.on")}</Button>
          </div>
          {pushMsg && <div className="mt-2 text-xs text-muted-foreground">{pushMsg}</div>}
          <Button size="sm" variant="ghost" className="mt-1" onClick={() => pushTest.mutate()}>{t("notify.push.test")}{pushTest.data ? ` (vapid ${pushTest.data.sent} · onesignal ${pushTest.data.onesignal ? "ok" : "—"})` : ""}</Button>
        </div>
        <label className="block">
          <div className="mb-1 text-xs text-muted-foreground">Telegram chat id {ch && !ch.telegram && <span className="text-warning">({t("notify.telegram.noToken")})</span>}</div>
          <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder="123456789" className="num h-9 w-full rounded-md border border-input bg-background px-3 outline-none focus:ring-2 focus:ring-ring/40" />
          <div className="mt-1 text-[11px] text-muted-foreground">{t("notify.telegram.howto")}</div>
        </label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={email} onChange={(e) => setEmail(e.target.checked)} /> {t("notify.email")} ({q.data?.email}) {ch && !ch.email && <span className="text-xs text-warning">({t("notify.email.noSmtp")})</span>}</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={brief} onChange={(e) => setBrief(e.target.checked)} /> {t("notify.brief")}</label>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>{t("common.save")}</Button>
          <Button size="sm" variant="outline" onClick={() => test.mutate()} disabled={test.isPending}>{t("notify.test")}</Button>
          {test.data && <span className="text-xs text-muted-foreground">telegram: {String(test.data.telegram ?? "—")} · e-mail: {String(test.data.email ?? "—")}</span>}
          {save.isSuccess && <span className="text-xs text-positive">{t("common.saved")}</span>}
        </div>
      </div>
    </Section>
  )
}
