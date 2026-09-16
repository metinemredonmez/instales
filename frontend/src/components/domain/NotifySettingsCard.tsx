import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { api } from "@/lib/api"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { disablePush, enablePush, pushState, pushSupported } from "@/lib/push"
import { withOneSignal } from "@/lib/onesignal"

/** Where alerts and the morning brief get delivered. Telegram is free and instant; e-mail needs SMTP on the server. */
export function NotifySettingsCard() {
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
    setPushMsg(r === "ok" ? "Bu cihaza bildirim açıldı" : r === "denied" ? "Tarayıcı izni reddedildi" : r === "unsupported" ? "Bu cihaz/tarayıcı desteklemiyor (HTTPS gerekir)" : "Sunucuda push anahtarı tanımlı değil")
  }
  const pushTest = useMutation({ mutationFn: api.pushTest })
  return (
    <Section title="Bildirim kanalları" hint="alarm kuralları + sabah brifingi">
      <div className="space-y-3 p-4 text-sm">
        <div className="rounded-md border border-border bg-card p-3">
          <div className="flex items-center justify-between gap-2">
            <div>
              <div className="font-medium">📱 Bu cihaza bildirim (uygulama gibi)</div>
              <div className="text-[11px] text-muted-foreground">Telefonda: siteyi aç → paylaş/menü → "Ana ekrana ekle" → sonra bu düğme. {!pushSupported() && "Şu an HTTPS olmadığı için kapalı."}</div>
            </div>
            <Button size="sm" variant={push === "on" ? "outline" : "default"} onClick={togglePush} disabled={push === "…"}>{push === "on" ? "Kapat" : "Aç"}</Button>
          </div>
          {pushMsg && <div className="mt-2 text-xs text-muted-foreground">{pushMsg}</div>}
          <Button size="sm" variant="ghost" className="mt-1" onClick={() => pushTest.mutate()}>Cihaza test gönder{pushTest.data ? ` (vapid ${pushTest.data.sent} · onesignal ${pushTest.data.onesignal ? "ok" : "—"})` : ""}</Button>
        </div>
        <label className="block">
          <div className="mb-1 text-xs text-muted-foreground">Telegram chat id {ch && !ch.telegram && <span className="text-warning">(sunucuda bot token tanımlı değil)</span>}</div>
          <input value={chat} onChange={(e) => setChat(e.target.value)} placeholder="123456789" className="num h-9 w-full rounded-md border border-input bg-background px-3 outline-none focus:ring-2 focus:ring-ring/40" />
          <div className="mt-1 text-[11px] text-muted-foreground">Telegram'da bota /start yaz; chat id'ni @userinfobot ile öğrenebilirsin.</div>
        </label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={email} onChange={(e) => setEmail(e.target.checked)} /> E-posta ile gönder ({q.data?.email}) {ch && !ch.email && <span className="text-xs text-warning">(SMTP tanımlı değil)</span>}</label>
        <label className="flex items-center gap-2"><input type="checkbox" checked={brief} onChange={(e) => setBrief(e.target.checked)} /> Sabah brifingini gönder (08:30)</label>
        <div className="flex flex-wrap items-center gap-2">
          <Button size="sm" onClick={() => save.mutate()} disabled={save.isPending}>Kaydet</Button>
          <Button size="sm" variant="outline" onClick={() => test.mutate()} disabled={test.isPending}>Test gönder</Button>
          {test.data && <span className="text-xs text-muted-foreground">telegram: {String(test.data.telegram ?? "—")} · e-posta: {String(test.data.email ?? "—")}</span>}
          {save.isSuccess && <span className="text-xs text-positive">kaydedildi</span>}
        </div>
      </div>
    </Section>
  )
}
