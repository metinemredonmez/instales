import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { cn } from "@/lib/utils"
import { RuntimeSettingsForm } from "@/components/domain/RuntimeSettingsForm"

/** Read-only view of what the server has configured (booleans only — secrets never leave the server). */
export function AdminSettingsPage() {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["admin", "config"], queryFn: api.adminConfig })
  const c = q.data
  if (!c) return <div className="text-sm text-muted-foreground">…</div>
  const On = ({ v, label }: { v: boolean; label: string }) => (
    <span className={cn("inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5 text-xs", v ? "border-positive/40 text-positive" : "border-border text-muted-foreground")}>
      <span className={cn("size-1.5 rounded-full", v ? "bg-positive" : "bg-muted-foreground")} />{label}
    </span>
  )
  const Row = ({ k, v }: { k: string; v: React.ReactNode }) => (
    <div className="flex flex-wrap items-center justify-between gap-2 px-4 py-2 text-sm"><span className="text-muted-foreground">{k}</span><span className="font-mono text-xs">{v}</span></div>
  )
  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.settings.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("admin.settings.sub")}</p>
      </div>
      <RuntimeSettingsForm />
      <div className="grid gap-5 lg:grid-cols-2">
        <Section title={t("admin.settings.server")}>
          <div className="divide-y divide-border/60">
            <Row k={t("admin.settings.environment")} v={c.environment} />
            <Row k={t("admin.settings.publicUrl")} v={c.public_url} />
            <Row k={t("admin.settings.database")} v={c.database} />
            <Row k={t("admin.settings.registration")} v={<On v={c.allow_registration} label={c.allow_registration ? t("common.open") : t("common.closed")} />} />
          </div>
        </Section>
        <Section title={t("admin.settings.sources")}>
          <div className="divide-y divide-border/60">
            <Row k="KAP" v={c.kap_adapter + (c.kap_api_base_url ? ` · ${c.kap_api_base_url}` : "")} />
            <Row k="SEC 13F" v={`${c.sec_adapter} · CIK ${c.sec_ciks.join(", ")}`} />
            <Row k={t("admin.settings.news")} v={<span className="flex gap-1.5"><On v={c.news.enabled} label="RSS" /><On v={c.news.newsapi} label="NewsAPI" /></span>} />
          </div>
        </Section>
        <Section title={t("admin.settings.ai")}>
          <div className="divide-y divide-border/60">
            <Row k={t("admin.settings.model")} v={`${c.ai.provider} · ${c.ai.model}`} />
            <Row k={t("admin.settings.aiKey")} v={<On v={c.ai.configured} label={c.ai.configured ? t("common.configured") : t("common.missing")} />} />
            <Row k={t("admin.settings.newsEnrich")} v={<On v={c.ai.news_enrich} label={c.ai.news_enrich ? t("common.on") : t("common.off")} />} />
            <Row k={t("admin.settings.tts")} v={c.tts.provider ?? t("admin.settings.ttsBrowser")} />
            <Row k={t("admin.settings.voices")} v={<span className="flex flex-wrap gap-1.5">{Object.entries(c.tts.voices).map(([k, v]) => <On key={k} v={v} label={k.replace("_", " ")} />)}</span>} />
          </div>
        </Section>
        <Section title={t("admin.settings.channels")}>
          <div className="flex flex-wrap gap-1.5 p-4">
            <On v={c.channels.telegram} label="Telegram" />
            <On v={c.channels.email} label="SMTP" />
            <On v={c.channels.web_push} label="Web Push (VAPID)" />
            <On v={c.channels.onesignal} label="OneSignal" />
          </div>
        </Section>
      </div>
      <p className="text-xs text-muted-foreground">{t("admin.settings.envNote")}</p>
    </>
  )
}
