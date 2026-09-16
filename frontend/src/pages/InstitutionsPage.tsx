import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { api } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n } from "@/lib/i18n"
import { fmtDate, fmtLots } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { Flow } from "@/components/domain/badges"
import { EventRow } from "@/components/domain/EventRow"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

export function InstitutionsPage() {
  const { market } = useMarket()
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["institutions", market], queryFn: () => api.institutions(market) })
  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-semibold tracking-tight">{market === "TR" ? t("inst.title.tr") : t("inst.title.us")}</h1><p className="text-sm text-muted-foreground">{t("inst.sub")}</p></div>
      <Section title={t("inst.section")} hint={`${q.data?.length ?? 0}`}>
        <table className="w-full text-sm">
          <thead className="text-[11px] uppercase tracking-wider text-muted-foreground"><tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">{t("inst.institution")}</th><th className="px-2 py-2 text-right font-medium">{t("common.fund")}</th><th className="px-2 py-2 text-right font-medium">{t("inst.disclosures")}</th><th className="px-4 py-2 text-right font-medium">{t("common.status")}</th></tr></thead>
          <tbody>
            {q.data?.map((i) => (
              <tr key={i.code} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                <td className="px-4 py-2.5"><Link to={`/institutions/${i.code}`} className="font-semibold hover:underline">{i.name}</Link><div className="text-xs text-muted-foreground">{i.kind.replaceAll("_", " ").toLowerCase()}</div></td>
                <td className="num px-2 py-2.5 text-right">{i.funds}</td>
                <td className="num px-2 py-2.5 text-right">{i.events}</td>
                <td className="px-4 py-2.5 text-right text-xs">{i.is_verified ? <span className="text-positive">{t("inst.verified")}</span> : <span className="text-warning">{t("inst.pending")}</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>
    </div>
  )
}

export function InstitutionPage() {
  const { code = "" } = useParams()
  const { market } = useMarket()
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["institution", market, code], queryFn: () => api.institution(market, code) })
  if (q.isLoading) return <Skeleton className="h-96" />
  if (!q.data) return <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">{t("inst.notFound")}</div>
  const d = q.data
  return (
    <div className="space-y-5">
      <div>
        <div className="text-xs text-muted-foreground">{d.kind.replaceAll("_", " ").toLowerCase()} · {t("fund.activityPeriod")} {fmtDate(d.activity_period_end)}</div>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">{d.name}</h1>
        <div className="mt-2 flex flex-wrap gap-1.5">{d.funds.map((f) => <Link key={f.code} to={`/funds/${f.code}`} className="rounded bg-secondary px-2 py-0.5 font-mono text-xs hover:underline" title={f.name}>{f.code}</Link>)}</div>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <AggTable title={t("inst.topIncreased")} rows={d.top_increased} market={market} />
        <AggTable title={t("inst.topReduced")} rows={d.top_reduced} market={market} negative />
      </div>
      <Section title={t("common.kapDisclosures")} hint={`${d.events.length}`}>{d.events.map((ev) => <EventRow key={ev.id} ev={ev} />)}{d.events.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">{t("common.none")}</div>}</Section>
    </div>
  )
}

function AggTable({ title, rows, market, negative }: { title: string; rows: { symbol: string; delta_qty: number; delta_value: number; funds_increasing: number; funds_reducing: number }[]; market: string; negative?: boolean }) {
  const { t } = useI18n()
  return (
    <Section title={title} hint={t("inst.total")}>
      {rows.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">{t("common.none")}</div>}
      {rows.length > 0 && (
        <table className="w-full text-sm">
          <tbody>
            {rows.map((r) => (
              <tr key={r.symbol} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                <td className="px-4 py-2"><Link to={`/stocks/${r.symbol}`} className="font-semibold hover:underline">{r.symbol}</Link></td>
                <td className={cn("num px-2 py-2 text-right", negative ? "text-negative" : "text-positive")}>{fmtLots(r.delta_qty)}</td>
                <td className="px-2 py-2 text-right"><Flow value={r.delta_value} market={market} /></td>
                <td className="num px-4 py-2 text-right text-xs text-muted-foreground"><span className="text-positive">{r.funds_increasing}↑</span> <span className="text-negative">{r.funds_reducing}↓</span></td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </Section>
  )
}
