import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { useState } from "react"
import { ChevronDown } from "lucide-react"
import { api, MARKET_WINDOW_DAYS, type PositionChange, type ScoreDetail } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n, type T } from "@/lib/i18n"
import { fmtDate, fmtLots } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { ActivityBadge, ConfidenceBadge, Flow, ScorePill, SignalBadge } from "@/components/domain/badges"
import { EventRow } from "@/components/domain/EventRow"
import { StockChart } from "@/components/domain/StockChart"
import { Timeline } from "@/components/domain/Timeline"
import { AiNoteCard } from "@/components/domain/AiNoteCard"
import { WatchButton } from "@/components/domain/WatchButton"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const componentLabel = (t: T): Record<string, string> => ({
  breadth: t("score.c.breadth"),
  net_flow: t("score.c.netFlow"),
  persistence: t("score.c.persistence"),
  new_positions: t("score.c.newPositions"),
  conviction: t("score.c.conviction"),
  freshness: t("score.c.freshness"),
})

export function StockPage() {
  const { symbol = "" } = useParams()
  const { market } = useMarket()
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["stock", market, symbol], queryFn: () => api.stock(market, symbol), refetchInterval: 60_000, placeholderData: (prev) => prev })

  if (q.isLoading) return <Skeleton className="h-96" />
  if (q.isError || !q.data)
    return (
      <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">
        <b className="text-foreground">{symbol}</b> {t("stock.notFound")} <Link className="text-primary underline" to={`/funds/${symbol}`}>{t("stock.tryFund")}</Link>
      </div>
    )
  const d = q.data
  const sm = d.scores.SMART_MONEY
  const cs = d.scores.CONSENSUS
  const act = sm?.why.activity

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="text-xs text-muted-foreground">{d.market} · {t("stock.asOf")} {fmtDate(d.as_of)} · {t("stock.latestPeriod")} {fmtDate(d.latest_period_end)}</div>
          <h1 className="mt-1 text-3xl font-semibold tracking-tight">{d.symbol} <span className="text-lg font-normal text-muted-foreground">{d.name !== d.symbol ? d.name : ""}</span></h1>
        </div>
        <div className="flex flex-wrap items-center gap-2">{d.signals.map((s) => <SignalBadge key={s.type + s.window_end} type={s.type} />)}<WatchButton symbol={d.symbol} market={market} /></div>
      </div>

      <div className="rise-stagger grid gap-3 md:grid-cols-3">
        <ScoreCard title="Smart Money Score" detail={sm} />
        <ScoreCard title={t("stock.consensus")} detail={cs} />
        <div className="rounded-lg border border-border bg-card p-4">
          <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{t("stock.activityWindow", { d: MARKET_WINDOW_DAYS[d.market] ?? MARKET_WINDOW_DAYS[market] })}</div>
          {act ? (
            <div className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-sm">
              <Row k={t("common.increasing")} v={act.funds_increasing} tone="pos" />
              <Row k={t("common.reducing")} v={act.funds_reducing} tone="neg" />
              <Row k={t("common.newPosition")} v={act.funds_new} tone="pos" />
              <Row k={t("common.fullExit")} v={act.funds_exited} tone="neg" />
              <Row k={t("common.netFlow")} v={<Flow value={act.net_flow_value} market={market} />} />
              <Row k={t("stock.persistence")} v={act.persistence_periods} />
              <div className="col-span-2 mt-2 flex flex-wrap gap-1.5 text-[11px]">
                {Object.entries(act.flow_by_confidence).map(([c, v]) => (
                  <span key={c} className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5">
                    <ConfidenceBadge value={c as "EXACT"} /> <Flow value={Math.abs(Number(v))} market={market} className="text-foreground" />
                  </span>
                ))}
              </div>
            </div>
          ) : (
            <div className="mt-2 text-sm text-muted-foreground">{t("stock.noActivity")}</div>
          )}
        </div>
      </div>

      <AiNoteCard market={market} symbol={d.symbol} title={`${d.symbol} · ${t("stock.aiTitle")}`} />

      <StockChart symbol={d.symbol} market={market} />

      <div className="grid gap-5 lg:grid-cols-2">
        <Section title={t("stock.topBuyers")} hint={t("stock.lastPeriod")}><ChangeTable rows={d.top_buyers} market={market} /></Section>
        <Section title={t("stock.topSellers")} hint={t("stock.lastPeriod")}><ChangeTable rows={d.top_sellers} market={market} /></Section>
      </div>

      {d.signals.length > 0 && (
        <Section title={t("common.signals")} hint={t("stock.withEvidence")}>
          <ul className="divide-y divide-border/60">
            {d.signals.map((s) => (
              <li key={s.type + s.window_end} className="px-4 py-3">
                <div className="flex items-center gap-3">
                  <SignalBadge type={s.type} />
                  <span className="text-xs text-muted-foreground">{fmtDate(s.window_start)} → {fmtDate(s.window_end)}</span>
                  <span className="ml-auto num text-sm">{s.strength}/100</span>
                  <ConfidenceBadge value={s.confidence} />
                </div>
                <pre className="mt-2 overflow-x-auto rounded bg-muted/60 p-2 text-[11px] text-muted-foreground">{JSON.stringify(s.evidence, null, 1)}</pre>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <RelatedNews symbol={d.symbol} market={market} />

      <Timeline symbol={d.symbol} market={market} />

      <Section title={t(d.market === "US" ? "common.secDisclosures" : "common.kapDisclosures")} hint={`${d.events.length}`}>
        {d.events.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">{t("stock.noEvents")}</div>}
        {d.events.map((ev) => <EventRow key={ev.id} ev={ev} market={d.market} />)}
      </Section>
    </div>
  )
}

function RelatedNews({ symbol, market }: { symbol: string; market: "TR" | "US" }) {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["news", market, symbol], queryFn: () => api.news(market, symbol, 10) })
  if (!q.data?.length) return null
  return (
    <Section title={t("stock.relatedNews")} hint={t("stock.relatedNews.hint")}>
      <ul className="divide-y divide-border/60 text-sm">
        {q.data.map((n) => (
          <li key={n.id} className="flex items-center gap-3 px-4 py-2">
            <span className="num w-24 shrink-0 text-xs text-muted-foreground">{fmtDate(n.published_at)}</span>
            <a href={n.url} target="_blank" rel="noreferrer" className="truncate hover:underline">{n.title}</a>
            <span className="ml-auto shrink-0 text-xs text-muted-foreground">{n.source}</span>
          </li>
        ))}
      </ul>
    </Section>
  )
}

function Row({ k, v, tone }: { k: string; v: React.ReactNode; tone?: "pos" | "neg" }) {
  return (
    <>
      <span className="text-muted-foreground">{k}</span>
      <span className={cn("num text-right", tone === "pos" && "text-positive", tone === "neg" && "text-negative")}>{v}</span>
    </>
  )
}

function ScoreCard({ title, detail }: { title: string; detail?: ScoreDetail }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  if (!detail) return <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">{title}: —</div>
  const labels = componentLabel(t)
  const comps = Object.entries(detail.why.components).filter(([k]) => k in labels)
  const weights = detail.why.weights ?? {}
  return (
    <div className="rounded-lg border border-border bg-card p-4">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{title}</div>
      <div className="mt-1 flex items-center justify-between">
        <ScorePill value={detail.score} size="lg" />
        <button onClick={() => setOpen(!open)} className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
          {t("score.why", { n: Math.round(detail.score) })} <ChevronDown className={cn("size-3 transition", open && "rotate-180")} />
        </button>
      </div>
      <div className="mt-1 text-xs text-muted-foreground num">
        {t("score.raw")} {detail.raw.toFixed(1)} × {t("score.confidence")} {detail.why.confidence_multiplier.toFixed(2)}
      </div>
      {open && (
        <div className="mt-3 space-y-1.5 border-t border-border/60 pt-3">
          {comps.length === 0 && <div className="text-xs text-muted-foreground">{JSON.stringify(detail.why.components)}</div>}
          {comps.map(([k, v]) => (
            <div key={k} className="text-xs">
              <div className="flex justify-between"><span>{labels[k]}</span><span className="num text-muted-foreground">{Math.round(v * 100)}% · {t("score.weight")} {Math.round((weights[k] ?? 0) * 100)}</span></div>
              <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary" style={{ width: `${v * 100}%` }} /></div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

function ChangeTable({ rows, market }: { rows: PositionChange[]; market: string }) {
  const { t } = useI18n()
  if (rows.length === 0) return <div className="px-4 py-6 text-sm text-muted-foreground">{t("common.none")}</div>
  return (
    <table className="w-full text-sm">
      <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
        <tr className="border-b border-border/60">
          <th className="px-4 py-2 text-left font-medium">{t("common.fund")}</th>
          <th className="px-2 py-2 text-left font-medium">{t("common.move")}</th>
          <th className="px-2 py-2 text-right font-medium">{t("common.lots")}</th>
          <th className="px-2 py-2 text-right font-medium">{t("common.value")}</th>
          <th className="hidden px-4 py-2 text-right font-medium sm:table-cell">{t("common.weight")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((c) => (
          <tr key={c.fund + c.period_end} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
            <td className="px-4 py-2"><Link to={`/funds/${c.fund}`} className="font-mono font-semibold hover:underline">{c.fund}</Link></td>
            <td className="px-2 py-2"><ActivityBadge value={c.activity} /></td>
            <td className={cn("num px-2 py-2 text-right", c.delta_qty > 0 ? "text-positive" : "text-negative")}>{fmtLots(c.delta_qty)}</td>
            <td className="px-2 py-2 text-right"><Flow value={c.delta_value} market={market} /></td>
            <td className="num hidden px-4 py-2 text-right text-muted-foreground sm:table-cell">
              {c.from_weight_pct?.toFixed(1) ?? "0.0"}% → {c.to_weight_pct?.toFixed(1) ?? "0.0"}%
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}
