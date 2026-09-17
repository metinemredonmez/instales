import { useMutation, useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n } from "@/lib/i18n"
import { fmtDateTime } from "@/lib/format"
import { Section, Stat } from "@/components/layout/Section"
import { Button } from "@/components/ui/button"
import { PipelineButton } from "@/components/domain/PipelineButton"
import { FreshnessBar } from "@/components/domain/Freshness"
import { WarehouseCard } from "@/components/domain/WarehouseCard"

/** Data & pipeline: freshness per source, run the full pull, recompute signal outcomes, the DuckDB warehouse builds, quick counts. */
export function AdminOverviewPage() {
  const { t } = useI18n()
  const { market } = useMarket()
  const users = useQuery({ queryKey: ["admin", "users"], queryFn: api.adminUsers })
  const review = useQuery({ queryKey: ["admin", "review"], queryFn: api.adminReview })
  const status = useQuery({ queryKey: ["pipeline-status"], queryFn: api.adminPipelineStatus })
  const outcomes = useMutation({ mutationFn: api.adminComputeOutcomes })
  const audit = useQuery({ queryKey: ["admin", "audit"], queryFn: () => api.adminAudit(40), refetchInterval: 60_000 })
  const r = review.data
  const pending = (r?.instruments.length ?? 0) + (r?.funds.length ?? 0) + (r?.institutions.length ?? 0)
  const st = status.data

  return (
    <>
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("admin.overview.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("admin.overview.sub")}</p>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label={t("admin.stat.users")} value={users.data?.length ?? "—"} sub={`${users.data?.filter((u) => u.is_active).length ?? 0} ${t("admin.stat.active")}`} />
        <Stat label={t("admin.stat.review")} value={pending} sub={<Link to="/admin/review" className="text-primary hover:underline">{t("admin.stat.reviewLink")}</Link>} tone={pending ? "neg" : undefined} />
        <Stat label={t("admin.stat.parseErrors")} value={r?.failed_disclosures.length ?? "—"} />
        <Stat label={t("admin.stat.lastRun")} value={<span className="text-base">{st?.finished_at ? fmtDateTime(st.finished_at) : st?.running ? t("pipeline.running") : "—"}</span>} sub={st?.error ? <span className="text-negative">{st.error.split("\n").slice(-1)[0]}</span> : undefined} />
      </div>
      <Section title={t("admin.pipeline.title")} hint={t("admin.pipeline.hint")}>
        <div className="space-y-4 p-4">
          <FreshnessBar market={market} />
          <div className="flex flex-wrap items-center gap-2">
            <PipelineButton size="default" />
            <Button variant="outline" onClick={() => outcomes.mutate()} disabled={outcomes.isPending}>
              {t("admin.outcomes")}{outcomes.data ? ` (${outcomes.data.updated})` : ""}
            </Button>
          </div>
          {st?.result && <div className="text-xs text-muted-foreground">{t("pipeline.last")}: {Object.entries(st.result).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>}
          <p className="text-xs text-muted-foreground">{t("admin.pipeline.schedule")}</p>
        </div>
      </Section>
      <WarehouseCard />
      <Section title={t("admin.audit.title")} hint={t("admin.audit.hint")}>
        <ul className="divide-y divide-border/60 text-xs">
          {audit.data?.map((e) => (
            <li key={e.id} className="flex flex-wrap items-center gap-2 px-4 py-1.5">
              <span className="num w-28 shrink-0 text-muted-foreground">{fmtDateTime(e.created_at)}</span>
              <span className={`rounded-sm border px-1.5 py-px font-mono ${e.kind.includes("fail") || e.kind.includes("locked") ? "border-negative/40 text-negative" : e.kind.startsWith("admin") ? "border-warning/40 text-warning" : "border-border text-muted-foreground"}`}>{e.kind}</span>
              <span>{e.actor}</span>
              {e.subject && e.subject !== e.actor && <span className="text-muted-foreground">→ {e.subject}</span>}
              {e.ip && <span className="num text-muted-foreground">{e.ip}</span>}
              {e.detail && <span className="truncate text-muted-foreground">{e.detail}</span>}
            </li>
          ))}
          {audit.data?.length === 0 && <li className="px-4 py-6 text-muted-foreground">{t("common.none")}</li>}
        </ul>
      </Section>
    </>
  )
}
