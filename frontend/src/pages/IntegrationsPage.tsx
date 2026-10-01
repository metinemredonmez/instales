import { lazy, Suspense, useState } from "react"
import { useQuery, useQueryClient } from "@tanstack/react-query"
import { api, type WebhookEndpoint, type WebhookEvent } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { apiOrigin } from "@/components/integrations/swagger"

const SwaggerPanel = lazy(() => import("@/components/integrations/SwaggerPanel"))
const field = "w-full rounded-md border border-border bg-background px-3 py-2 text-sm"
const newEvent = () => JSON.stringify({ id: crypto.randomUUID(), type: "custom.event", data: { message: "Hello" } }, null, 2)
function parseEvent(text: string): WebhookEvent {
  const value: unknown = JSON.parse(text)
  if (!value || typeof value !== "object" || !("id" in value) || !("type" in value) || !("data" in value)) throw new Error("invalid event")
  const event = value as WebhookEvent
  if (typeof event.id !== "string" || !event.id || typeof event.type !== "string" || !event.type || !event.data || typeof event.data !== "object" || Array.isArray(event.data)) throw new Error("invalid event")
  return event
}

export default function IntegrationsPage() {
  const { t } = useI18n()
  const { user } = useAuth()
  const qc = useQueryClient()
  const [tab, setTab] = useState<"webhooks" | "swagger">("swagger")
  const [name, setName] = useState("")
  const [target, setTarget] = useState("")
  const [selected, setSelected] = useState("")
  const [secret, setSecret] = useState<string | null>(null)
  const [error, setError] = useState("")
  const [busy, setBusy] = useState(false)
  const key = ["webhooks", user?.id]
  const endpoints = useQuery({ queryKey: [...key, "endpoints"], queryFn: api.webhookEndpoints })
  const endpoint = endpoints.data?.find(e => e.id === selected) ?? endpoints.data?.[0]
  async function create() {
    setBusy(true); setError("")
    try {
      const created = await api.createWebhook(name.trim(), target.trim() || null)
      setSecret(created.signing_secret ?? null)
      setSelected(created.id); setName(""); setTarget("")
      await qc.invalidateQueries({ queryKey: key })
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <div className="space-y-5">
    <div><h1 className="text-2xl font-semibold tracking-tight">{t("integrations.title")}</h1><p className="text-sm text-muted-foreground">{t("integrations.sub")}</p></div>
    <div className="flex gap-2">
      <Button variant={tab === "swagger" ? "default" : "outline"} onClick={() => setTab("swagger")}>{t("integrations.swagger")}</Button>
      <Button variant={tab === "webhooks" ? "default" : "outline"} onClick={() => setTab("webhooks")}>{t("integrations.webhooks")}</Button>
    </div>
    {tab === "swagger" ? <>
      <p className="text-sm text-muted-foreground">{t("integrations.auth")}</p>
      <Suspense fallback={<p>{t("integrations.loading")}</p>}><SwaggerPanel /></Suspense>
    </> : <>
      <p className="text-sm text-muted-foreground">{t("integrations.worker")}</p>
      <Section title={t("integrations.new")}>
        <form className="grid items-end gap-3 p-4 sm:grid-cols-[1fr_2fr_auto]" onSubmit={e => { e.preventDefault(); void create() }}>
          <label className="space-y-1 text-sm">{t("integrations.name")}<input className={field} value={name} maxLength={80} required onChange={e => setName(e.target.value)} /></label>
          <label className="space-y-1 text-sm">{t("integrations.target")}<input className={field} type="url" placeholder="https://example.com/webhook" value={target} onChange={e => setTarget(e.target.value)} /></label>
          <Button disabled={busy || !name.trim()} type="submit">{t("integrations.create")}</Button>
        </form>
      </Section>
      {(error || endpoints.error) && <p role="alert" className="text-sm text-destructive">{error || endpoints.error?.message}</p>}
      {endpoints.isPending && <p>{t("integrations.loading")}</p>}
      {secret && <div className="space-y-2 rounded-md border border-primary bg-primary/5 p-4">
        <p className="text-sm">{t("integrations.secret")}</p><code className="block break-all select-all text-sm">{secret}</code>
        <Button size="sm" variant="outline" onClick={() => setSecret(null)}>{t("integrations.hide")}</Button>
      </div>}
      {!!endpoints.data?.length && <label className="block text-sm">{t("integrations.webhooks")}
        <select className={`${field} mt-1`} value={endpoint?.id ?? ""} onChange={e => { setSelected(e.target.value); setSecret(null) }}>
          {endpoints.data.map(e => <option key={e.id} value={e.id}>{e.name} · {t(e.enabled ? "integrations.active" : "integrations.paused")}</option>)}
        </select>
      </label>}
      {endpoints.data?.length === 0 && <p className="text-sm text-muted-foreground">{t("integrations.empty")}</p>}
      {endpoint && <EndpointPanel key={endpoint.id} endpoint={endpoint} onSecret={setSecret} />}
    </>}
  </div>
}

function EndpointPanel({ endpoint, onSecret }: { endpoint: WebhookEndpoint; onSecret: (value: string) => void }) {
  const { t } = useI18n()
  const { user } = useAuth()
  const qc = useQueryClient()
  const [event, setEvent] = useState(newEvent)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [before, setBefore] = useState<number>()
  const [detail, setDetail] = useState<number>()
  const key = ["webhooks", user?.id]
  const history = useQuery({ queryKey: [...key, endpoint.id, before], queryFn: () => api.webhookMessages(endpoint.id, before), refetchInterval: 10000 })
  const message = useQuery({ queryKey: [...key, "message", detail], queryFn: () => api.webhookMessage(detail!), enabled: detail !== undefined, refetchInterval: detail === undefined ? false : 10000 })
  async function run(action: () => Promise<unknown>, queued = false) {
    setBusy(true); setError(""); setNotice("")
    try {
      await action()
      if (queued) { setNotice(t("integrations.queued")); setBefore(undefined) }
      await qc.invalidateQueries({ queryKey: key })
    } catch (e) { setError(e instanceof Error ? e.message : String(e)) }
    finally { setBusy(false) }
  }
  return <div className="space-y-4">
    <Section title={endpoint.name}>
      <div className="space-y-3 p-4 text-sm">
        <div><p className="text-muted-foreground">{t("integrations.incoming")}</p><code className="break-all select-all">{apiOrigin()}{endpoint.incoming_path}</code></div>
        <div><p className="text-muted-foreground">{t("integrations.outgoing")}</p><code className="break-all">{endpoint.target_url || t("integrations.none")}</code></div>
        <p className="text-muted-foreground">{t("integrations.signing")}</p>
        <div className="flex flex-wrap gap-2">
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(() => api.enableWebhook(endpoint.id, !endpoint.enabled))}>{t(endpoint.enabled ? "integrations.pause" : "integrations.resume")}</Button>
          <Button size="sm" variant="outline" disabled={busy} onClick={() => void run(async () => { const e = await api.rotateWebhook(endpoint.id); if (e.signing_secret) onSecret(e.signing_secret) })}>{t("integrations.rotate")}</Button>
          <Button size="sm" variant="outline" disabled={busy || !endpoint.enabled || !endpoint.target_url} onClick={() => void run(() => api.testWebhook(endpoint.id), true)}>{t("integrations.test")}</Button>
        </div>
        <p className="text-xs text-muted-foreground">{t("integrations.rotateHint")}</p>
      </div>
    </Section>
    {endpoint.target_url && <form className="space-y-2" onSubmit={e => {
      e.preventDefault()
      let parsed: WebhookEvent
      try { parsed = parseEvent(event) } catch { setError(t("integrations.invalid")); return }
      void run(async () => { await api.sendWebhook(endpoint.id, parsed); setEvent(JSON.stringify({ ...parsed, id: crypto.randomUUID() }, null, 2)) }, true)
    }}>
      <label className="block text-sm">{t("integrations.event")}<textarea className={`${field} mt-1 font-mono`} rows={7} value={event} onChange={e => setEvent(e.target.value)} spellCheck={false} /></label>
      <Button disabled={busy || !endpoint.enabled} type="submit">{t("integrations.send")}</Button>
    </form>}
    {(error || history.error || message.error) && <p role="alert" className="text-sm text-destructive">{error || history.error?.message || message.error?.message}</p>}
    {notice && <p role="status" className="text-sm">{notice}</p>}
    <Section title={t("integrations.history")}>
      <div className="space-y-3 p-4">
        <Button size="sm" variant="outline" onClick={() => void qc.invalidateQueries({ queryKey: key })}>{t("integrations.refresh")}</Button>
        {history.isPending && <p>{t("integrations.loading")}</p>}
        {history.data?.length === 0 && <p className="text-sm text-muted-foreground">{t("integrations.noEvents")}</p>}
        <ul className="divide-y divide-border">{history.data?.map(row => <li key={row.id} className="flex flex-wrap items-center gap-3 py-3 text-sm">
          <button className="min-w-0 flex-1 text-left hover:underline" onClick={() => setDetail(row.id)}><span className="block break-all font-medium">{row.event_type}</span><span className="block break-all text-xs text-muted-foreground">{row.event_id} · {row.created_at} UTC</span></button>
          <span>{t(row.direction === "incoming" ? "integrations.incomingLabel" : "integrations.outgoingLabel")}</span>
          <span>{t(`integrations.${row.status}`)}</span><span className="text-muted-foreground">{t("integrations.attempts")}: {row.attempts}</span>
          {row.status === "failed" && <Button size="sm" variant="outline" disabled={busy || !endpoint.enabled} onClick={() => void run(() => api.retryWebhook(row.id), true)}>{t("integrations.retry")}</Button>}
        </li>)}</ul>
        <div className="flex gap-2">
          {before !== undefined && <Button size="sm" variant="outline" onClick={() => setBefore(undefined)}>{t("integrations.latest")}</Button>}
          {history.data?.length === 25 && <Button size="sm" variant="outline" onClick={() => setBefore(history.data!.at(-1)!.id)}>{t("integrations.older")}</Button>}
        </div>
      </div>
    </Section>
    {detail !== undefined && <Section title={t("integrations.details")}>
      <div className="space-y-3 p-4"><Button size="sm" variant="outline" onClick={() => setDetail(undefined)}>{t("integrations.close")}</Button>
        <pre className="max-h-96 overflow-auto rounded-md bg-muted p-3 text-xs">{message.data ? JSON.stringify(message.data, null, 2) : t("integrations.loading")}</pre>
      </div>
    </Section>}
  </div>
}
