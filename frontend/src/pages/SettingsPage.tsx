import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { CreditCard } from "lucide-react"
import { api, type PlanCode, type PlanSource } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { fmtDate } from "@/lib/format"
import { NotifySettingsCard } from "@/components/domain/NotifySettingsCard"
import { AccountSecurityCard } from "@/components/domain/AccountSecurityCard"
import { planName } from "@/components/domain/PlanGate"
import { Section } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"

/** Personal settings: delivery channels, account security, and the plan card (details and billing live on /plan). */
export function SettingsPage() {
  const { t } = useI18n()
  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-semibold tracking-tight">{t("settings.page.title")}</h1><p className="text-sm text-muted-foreground">{t("settings.page.sub")}</p></div>
      <div className="grid gap-5 lg:grid-cols-2">
        <NotifySettingsCard />
        <AccountSecurityCard />
        <PlanCard />
      </div>
    </div>
  )
}

/** The plan and where it comes from (own subscription / organisation seat / admin grant); /billing/me when the API has it, the session otherwise. */
function PlanCard() {
  const { t } = useI18n()
  const { user } = useAuth()
  const me = useQuery({ queryKey: ["billing", "me"], queryFn: api.billingMe, retry: false })
  const plan: PlanCode = me.data?.plan ?? user?.plan ?? "FREE"
  const source: PlanSource | undefined = me.data?.source ?? user?.plan_source
  const sub = me.data?.subscription ?? null
  return (
    <Section title={t("settings.plan.title")} hint={t("settings.plan.hint")}>
      <div className="flex flex-wrap items-center gap-3 p-4 text-sm">
        <span className="rounded-sm border border-primary/40 bg-primary/10 px-2 py-0.5 font-semibold text-primary">{planName(t, plan)}</span>
        {user?.role === "ADMIN" && <span className="rounded-sm border border-border px-1.5 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">{t("plan.admin")}</span>}
        {source && <span className="text-xs text-muted-foreground">{t(`plan.source.${source}`)}</span>}
        {sub?.current_period_end ? (
          <span className="text-xs text-muted-foreground">· {sub.provider === "manual" ? t("plan.manualUntil", { d: fmtDate(sub.current_period_end) }) : t("plan.renews", { d: fmtDate(sub.current_period_end) })}</span>
        ) : source === "manual" && user?.plan_until ? (
          <span className="text-xs text-muted-foreground">· {t("plan.manualUntil", { d: fmtDate(user.plan_until) })}</span>
        ) : null}
        <Button asChild size="sm" variant="outline" className="ml-auto"><Link to="/plan"><CreditCard /> {t("settings.plan.manage")}</Link></Button>
      </div>
    </Section>
  )
}
