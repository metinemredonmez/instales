import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { api, type Activity } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n } from "@/lib/i18n"
import { fmtDate, fmtLots, fmtQty } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { ActivityBadge, ConfidenceBadge, Flow } from "@/components/domain/badges"
import { EventRow } from "@/components/domain/EventRow"
import { WatchButton } from "@/components/domain/WatchButton"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const ORDER: Exclude<Activity, "HOLD">[] = ["NEW", "ADD", "REDUCE", "EXIT"]
const TITLE_KEY = { NEW: "fund.act.new", ADD: "fund.act.add", REDUCE: "fund.act.reduce", EXIT: "fund.act.exit" } as const

export function FundPage() {
  const { code = "" } = useParams()
  const { market } = useMarket()
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["fund", code], queryFn: () => api.fund(code) })
  if (q.isLoading) return <Skeleton className="h-96" />
  if (q.isError || !q.data) return <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground"><b className="text-foreground">{code}</b> {t("fund.notFound")}</div>
  const f = q.data
  const maxW = Math.max(...f.holdings.map((h) => h.weight_pct ?? 0), 1)

  return (
    <div className="space-y-5">
      <div>
        <div className="text-xs text-muted-foreground"><Link to={`/institutions/${f.institution.code}`} className="hover:underline">{f.institution.name}</Link> · {t("fund.portfolioDate")} {fmtDate(f.snapshot_as_of)} · {t("fund.activityPeriod")} {fmtDate(f.activity_period_end)}</div>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight"><span className="font-mono">{f.code}</span> <span className="text-lg font-normal text-muted-foreground">{f.name}</span></h1>
        <div className="mt-2 flex items-center gap-3 text-sm">{t("fund.portfolioValue")} <Flow value={f.total_value} market={market} className="text-foreground" /> <WatchButton fundCode={f.code} market={market} /><Link to={`/compare?a=${f.code}`} className="text-xs text-primary hover:underline">{t("fund.compareLink")} →</Link></div>
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_1fr]">
        <Section title={t("fund.activity")} hint={t("fund.activity.hint")} >
          <div className="divide-y divide-border/60">
            {ORDER.map((k) => (
              <div key={k} className="px-4 py-3">
                <div className="mb-1.5 flex items-center gap-2 text-xs text-muted-foreground"><ActivityBadge value={k} /> {t(TITLE_KEY[k])} <span className="num">({f.activity[k].length})</span></div>
                {f.activity[k].length === 0 ? (
                  <div className="text-xs text-muted-foreground/60">—</div>
                ) : (
                  <ul className="space-y-1">
                    {f.activity[k].map((c) => (
                      <li key={c.symbol} className="flex items-center gap-3 text-sm">
                        <Link to={`/stocks/${c.symbol}`} className="w-16 font-semibold hover:underline">{c.symbol}</Link>
                        <span className={cn("num", c.delta_qty > 0 ? "text-positive" : "text-negative")}>{fmtLots(c.delta_qty)}</span>
                        <Flow value={c.delta_value} market={market} className="text-xs" />
                        <span className="ml-auto num text-xs text-muted-foreground">{c.from_weight_pct?.toFixed(1) ?? "0.0"}% → {c.to_weight_pct?.toFixed(1) ?? "0.0"}%</span>
                        <ConfidenceBadge value={c.confidence} />
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </Section>

        <Section title={t("fund.portfolio")} hint={`${f.holdings.length} ${t("common.stock").toLowerCase()}`}>
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
              <tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">{t("common.stock")}</th><th className="px-2 py-2 text-right font-medium">{t("common.quantity")}</th><th className="px-2 py-2 text-right font-medium">{t("common.value")}</th><th className="px-4 py-2 text-right font-medium">{t("common.weight")}</th></tr>
            </thead>
            <tbody>
              {f.holdings.map((h) => (
                <tr key={h.symbol} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                  <td className="px-4 py-2"><Link to={`/stocks/${h.symbol}`} className="font-semibold hover:underline">{h.symbol}</Link></td>
                  <td className="num px-2 py-2 text-right">{fmtQty(h.quantity)}</td>
                  <td className="px-2 py-2 text-right"><Flow value={h.market_value} market={market} className="text-foreground" /></td>
                  <td className="px-4 py-2 text-right">
                    <span className="num">{h.weight_pct?.toFixed(1) ?? "—"}%</span>
                    <div className="ml-auto mt-1 h-1 w-16 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary/70" style={{ width: `${((h.weight_pct ?? 0) / maxW) * 100}%` }} /></div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      </div>

      <Section title={t("common.kapDisclosures")} hint={`${f.events.length}`}>
        {f.events.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">{t("fund.noEvents")}</div>}
        {f.events.map((ev) => <EventRow key={ev.id} ev={ev} />)}
      </Section>
    </div>
  )
}
