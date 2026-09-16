import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { api, type Market } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { disablePush, enablePush, pushState, pushSupported } from "@/lib/push"
import { useI18n } from "@/lib/i18n"

const MARKETS: Market[] = ["TR", "US"]

/** Where alerts and the morning brief get delivered. Telegram is free and instant; e-mail needs SMTP on the server. */
export function NotifySettingsCard() {
  const { t } = useI18n()
  const { user } = useAuth()
  const qc = useQueryClient()
  const q = useQuery({ queryKey: ["me-settings"], queryFn: api.mySettings })
  const [chat, setChat] = useState("")
  const [email, setEmail] = useState(false)
  const [brief, setBrief] = useState(true)
  const [briefMarkets, setBriefMarkets] = useState<Market[]>(["TR"])
  useEffect(() => { if (q.data) { setChat(q.data.notify_telegram_chat_id ?? ""); setEmail(q.data.notify_email); setBrief(q.data.notify_brief); setBriefMarkets(q.data.brief_markets ?? ["TR"]) } }, [q.data])
  const toggleMarket = (m: Market) => setBriefMarkets((cur) => (cur.includes(m) ? cur.filter((x) => x !== m) : [...cur, m]))
  const save = useMutation({ mutationFn: () => api.saveSettings({ notify_email: email, notify_telegram_chat_id: chat || null, notify_brief: brief, brief_markets: briefMarkets }), onSuccess: () => qc.invalidateQueries({ queryKey: ["me-settings"] }) })
  const test = useMutation({ mutationFn: api.testNotification })
  const ch = q.data?.channels
  const [push, setPush] = useState<"on" | "off" | "…">("…")
  const [pushMsg, setPushMsg] = useState("")
  useEffect(() => { pushState().then(setPush).catch(() => setPush("off")) }, [])
  // enablePush picks the one configured path (OneSignal or VAPID) — prompt, subscribe, done.
  const togglePush = async () => {
    if (push === "on") { await disablePush().catch(() => {}); setPush("off"); setPushMsg(""); return }
    const r = await enablePush(user?.id).catch(() => "disabled" as const)
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
            <Button size="sm" variant={push === "on" ? "outline" : "default"} onClick={togglePush} disabled={push === "…"}>{push === "on" ? t("notify.push.turnOff") : t("notify.push.turnOn")}</Button>
          </div>
          {pushMsg && <div className="mt-2 text-xs text-muted-foreground">{pushMsg}</div>}
          {pushTest.data && !pushTest.data.onesignal && pushTest.data.sent === 0 && <div className="mt-1 text-xs text-negative">{pushTest.data.onesignal_error ?? t("notify.push.testFailed")}</div>}
          <Button size="sm" variant="ghost" className="mt-1" onClick={() => pushTest.mutate()}>{t("notify.push.test")}{pushTest.data ? ` (${pushTest.data.onesignal ? "OneSignal ✓" : pushTest.data.sent > 0 ? `VAPID ${pushTest.data.sent} ✓` : "✗"})` : ""}</Button>
        </div>
        <label className="block">
          <div className="mb-1 text-xs text-muted-foreground">Telegram chat id {ch && !ch.telegram && <span className="text-warning">({t("notify.telegram.noToken")})</span>}</div>
          <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder="123456789" className="num h-9 w-full rounded-md border border-input bg-background px-3 outline-none focus:ring-2 focus:ring-ring/40" />
          <div className="mt-1 text-[11px] text-muted-foreground">{t("notify.telegram.howto")}</div>
        </label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={email} onChange={(e) => setEmail(e.target.checked)} /> {t("notify.email")} ({q.data?.email}) {ch && !ch.email && <span className="text-xs text-warning">({t("notify.email.noSmtp")})</span>}</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={brief} onChange={(e) => setBrief(e.target.checked)} /> {t("notify.brief")}</label>
        <div className="flex flex-wrap items-center gap-3 pl-6">
          <span className="text-xs text-muted-foreground">{t("notify.briefMarkets")}</span>
          {MARKETS.map((m) => (
            <label key={m} className="flex items-center gap-1.5"><input type="checkbox" checked={briefMarkets.includes(m)} disabled={!brief} onChange={() => toggleMarket(m)} /> <span className="rounded-sm bg-muted px-1 py-px font-mono text-[10px] tracking-wider text-muted-foreground">{m}</span> {m === "TR" ? "BIST" : "Global"}</label>
          ))}
          <span className="text-[11px] text-muted-foreground">{t("notify.briefMarkets.hint")}</span>
        </div>
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
