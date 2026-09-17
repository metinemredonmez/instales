import { useQuery } from "@tanstack/react-query"
import { api, type ProvidersStatus } from "@/lib/api"
import { fmtDateTime } from "@/lib/format"
import { useI18n } from "@/lib/i18n"
import { providerLabel } from "@/lib/quotes"
import type { Key } from "@/i18n/tr"
import { Section } from "@/components/layout/Section"
import { cn } from "@/lib/utils"

/**
 * Admin → Ayarlar: what the price feed is doing right now, from /admin/providers every 30 s. Left half is the active
 * provider's own report (configured / connected / delay, last tick, error or note as the adapter phrased it — its
 * `name` is the provider key, so the human label comes from the same quotes.src.* strings the header strip uses, and a
 * choice that fell back to another provider is named next to it). Every process has its own provider instance, so the
 * report is the feed process's while the feed runs and the answering API worker's otherwise — the head says which.
 * Right half is the feed process heartbeat the API read back from app_settings, with the feed's own last error when it
 * is not the provider's. Nothing here is computed client-side — `running` is the server's verdict — so a feed that
 * died shows as stopped as soon as the API says so.
 */
export function ProviderStatusCard() {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["admin", "providers"], queryFn: api.adminProviders, refetchInterval: 30_000 })
  return (
    <Section title={t("admin.providers.title")} hint={t("admin.providers.hint")}>
      {q.data ? <Body p={q.data} /> : q.isError ? <p className="p-4 text-xs text-negative">{t("admin.providers.loadError")}</p> : <p className="p-4 text-sm text-muted-foreground">…</p>}
    </Section>
  )
}

const DELAY_KEY: Record<ProvidersStatus["price"]["status"]["delay"], Key> = { realtime: "admin.providers.delay.realtime", delayed: "admin.providers.delay.delayed", eod: "admin.providers.delay.eod" }

function Body({ p }: { p: ProvidersStatus }) {
  const { t } = useI18n()
  const s = p.price.status
  const f = p.feed
  return (
    <div className="grid divide-y divide-border/60 lg:grid-cols-2 lg:divide-x lg:divide-y-0">
      <div>
        <Head>
          {t("admin.providers.price")}
          {/* Whose instance is reporting: the feed's (it drives the strip) or, while the feed is down, this API worker's. */}
          {p.price.status_from && <span className="ml-2 normal-case tracking-normal">· {t(p.price.status_from === "feed" ? "admin.providers.seenBy.feed" : "admin.providers.seenBy.api")}</span>}
        </Head>
        <div className="divide-y divide-border/60">
          <Row k={t("admin.providers.active")} v={
            <span className="flex flex-wrap items-center gap-1.5">
              <span className="text-foreground">{providerLabel(p.price.active, t)}</span>
              <span className="font-mono text-[10px] text-muted-foreground">{s.name}</span>
              {/* The admin chose a provider that is not configured, so another one answers — say so, do not hide the fallback. */}
              {p.price.selected && p.price.selected !== p.price.active && <span className="text-warning">{t("admin.providers.fallback", { p: providerLabel(p.price.selected, t) })}</span>}
            </span>
          } />
          <Row k={t("admin.providers.available")} v={<span className="font-mono text-xs">{p.price.available.join(", ")}</span>} />
          <Row k={t("common.status")} v={
            <span className="flex flex-wrap gap-1.5">
              <Chip tone={s.configured ? "pos" : "muted"} label={t(s.configured ? "admin.providers.configured" : "admin.providers.notConfigured")} />
              <Chip tone={s.connected ? "pos" : "muted"} label={t(s.connected ? "admin.providers.connected" : "admin.providers.disconnected")} />
              {/* The delay is a claim about prints; a provider that has never connected has none to make, so no positive colour. */}
              <Chip tone={!s.connected ? "muted" : s.delay === "realtime" ? "pos" : "warn"} label={t(DELAY_KEY[s.delay])} />
            </span>
          } />
          <Row k={t("admin.providers.lastTick")} v={<span className="num">{s.last_tick_at ? fmtDateTime(s.last_tick_at) : "—"}</span>} />
          {s.error && <Row k={t("admin.providers.error")} v={<span className="text-negative">{s.error}</span>} />}
          {s.note && <Row k={t("admin.providers.note")} v={<span className="text-muted-foreground">{s.note}</span>} />}
        </div>
      </div>
      <div>
        <Head>{t("admin.providers.feed")}</Head>
        <div className="divide-y divide-border/60">
          <Row k={t("common.status")} v={<Chip tone={f.running ? "pos" : "muted"} label={t(f.running ? "admin.providers.running" : "admin.providers.stopped")} />} />
          <Row k={t("admin.providers.lastRun")} v={<span className="num">{f.last_run_at ? fmtDateTime(f.last_run_at) : "—"}</span>} />
          <Row k={t("admin.providers.interval")} v={<span className="num">{t("admin.providers.intervalValue", { s: f.interval_s })}</span>} />
          <Row k={t("admin.providers.published")} v={<span className="num">{f.published}</span>} />
          {/* The feed's own failure (a publish or heartbeat problem); the provider's error already has its row on the left. */}
          {f.error && f.error !== s.error && <Row k={t("admin.providers.error")} v={<span className="text-negative">{f.error}</span>} />}
        </div>
      </div>
    </div>
  )
}

const Head = ({ children }: { children: React.ReactNode }) => <div className="px-4 pt-3 text-[11px] uppercase tracking-wider text-muted-foreground">{children}</div>

const Row = ({ k, v }: { k: string; v: React.ReactNode }) => (
  <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-sm"><span className="text-muted-foreground">{k}</span><span className="text-xs">{v}</span></div>
)

/** Same dot-chip as the read-only config rows on the page; `warn` is for a delayed/end-of-day feed — a fact, not a fault. */
const CHIP = { pos: ["border-positive/40 text-positive", "bg-positive"], warn: ["border-warning/40 text-warning", "bg-warning"], muted: ["border-border text-muted-foreground", "bg-muted-foreground"] } as const
function Chip({ tone, label }: { tone: keyof typeof CHIP; label: string }) {
  const [box, dot] = CHIP[tone]
  return <span className={cn("inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-xs", box)}><span className={cn("size-1.5 rounded-full", dot)} />{label}</span>
}
