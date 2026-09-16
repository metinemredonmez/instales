import { useQuery } from "@tanstack/react-query"
import { useEffect, useState } from "react"
import { Link } from "react-router-dom"
import { api, type RadarRow } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { fmtDate } from "@/lib/format"
import { Section, Stat } from "@/components/layout/Section"
import { Flow, ScorePill, SignalBadge, ConfidenceBadge } from "@/components/domain/badges"
import { EventRow } from "@/components/domain/EventRow"
import { FreshnessBar } from "@/components/domain/Freshness"
import { PipelineButton } from "@/components/domain/PipelineButton"
import { AiNoteCard } from "@/components/domain/AiNoteCard"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

// null = the market's own score window (TR 30D, US 100D ≈ latest 13F quarter)
const WINDOWS: Record<string, { label: string; days: number | null }[]> = {
  TR: [{ label: "Bugün", days: 1 }, { label: "7G", days: 7 }, { label: "30G", days: null }, { label: "3A", days: 90 }],
  US: [{ label: "Son 13F", days: null }, { label: "6A", days: 180 }, { label: "1Y", days: 365 }],
}

export function RadarPage() {
  const { market } = useMarket()
  const [window, setWindow] = useState<number | null>(null)
  useEffect(() => setWindow(null), [market])
  const radar = useQuery({ queryKey: ["radar", market, window], queryFn: () => api.radar(market, 15, window) })
  const perf = useQuery({ queryKey: ["signal-perf", market], queryFn: () => api.signalPerformance(market) })
  const events = useQuery({ queryKey: ["events", market, 8], queryFn: () => api.events(market, 8), refetchInterval: 15_000 })

  if (radar.isLoading) return <RadarSkeleton />
  if (radar.isError || !radar.data) return <Empty market={market} />
  const r = radar.data
  if (!r.as_of) return <Empty market={market} />

  const totalIn = r.accumulated.reduce((s, x) => s + x.net_flow_value, 0)
  const totalOut = r.distributed.reduce((s, x) => s + x.net_flow_value, 0)
  const newPos = r.accumulated.reduce((s, x) => s + x.funds_new, 0)
  const exits = r.distributed.reduce((s, x) => s + x.funds_exited, 0)

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className="inline-flex items-center gap-1.5"><span className="size-1.5 animate-pulse rounded-full bg-positive" /> LIVE</span>
            <span>·</span>
            <span>Son hesaplama {fmtDate(r.as_of)}</span>
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{market === "TR" ? "Bugün fonlar ne yapıyor?" : "What are institutions doing?"}</h1>
        </div>
        <div className="flex rounded-md border border-border p-0.5 text-xs">
          {(WINDOWS[market] ?? WINDOWS.TR).map((w) => (
            <button key={w.label} onClick={() => setWindow(w.days)} className={cn("rounded-[5px] px-3 py-1 font-medium transition", window === w.days ? "bg-secondary" : "text-muted-foreground hover:text-foreground")}>{w.label}</button>
          ))}
        </div>
      </div>
      <div className="flex flex-wrap items-center justify-between gap-2"><FreshnessBar market={market} /><PipelineButton /></div>

      <AiNoteCard market={market} title="Sabah brifingi" />

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Net kurumsal giriş" value={<Flow value={totalIn} market={market} className="text-2xl" />} sub={`${r.accumulated.length} hisse toplanıyor`} />
        <Stat label="Net kurumsal çıkış" value={<Flow value={totalOut} market={market} className="text-2xl" />} sub={`${r.distributed.length} hisse dağıtılıyor`} />
        <Stat label="Yeni pozisyon" value={newPos} sub="fon × hisse, son dönem" tone="pos" />
        <Stat label="Tam çıkış" value={exits} sub="fon × hisse, son dönem" tone="neg" />
      </div>

      <div className="grid gap-5 lg:grid-cols-[1fr_360px]">
        <div className="space-y-5">
          <Section title="En çok toplananlar" hint={`${r.window_days} gün · net akış${window !== null ? ` · skorlar ${market === "US" ? "100" : "30"}G` : ""}`}>
            <FlowTable rows={r.accumulated} market={market} />
          </Section>
          <Section title="En çok satılanlar" hint={`${r.window_days} gün · net akış`}>
            <FlowTable rows={r.distributed} market={market} negative />
          </Section>
        </div>
        <div className="space-y-5">
          <Section title="Aktif sinyaller" hint={`${r.signals.length}`}>
            <ul className="divide-y divide-border/60">
              {r.signals.map((s) => (
                <li key={`${s.symbol}-${s.type}`} className="flex items-center gap-3 px-4 py-2.5">
                  <Link to={`/stocks/${s.symbol}`} className="w-14 font-semibold hover:underline">{s.symbol}</Link>
                  <SignalBadge type={s.type} />
                  <span className="ml-auto num text-sm text-muted-foreground">{s.strength}</span>
                  <ConfidenceBadge value={s.confidence} />
                </li>
              ))}
              {r.signals.length === 0 && <li className="px-4 py-6 text-sm text-muted-foreground">Sinyal yok.</li>}
            </ul>
          </Section>
          {perf.data && perf.data.by_type.length > 0 && (
            <Section title="Sinyal performansı" hint="ortalama ileri getiri">
              <table className="w-full text-xs">
                <thead className="text-[10px] uppercase tracking-wider text-muted-foreground"><tr className="border-b border-border/60"><th className="px-4 py-1.5 text-left font-medium">Sinyal</th><th className="px-1 py-1.5 text-right font-medium">n</th><th className="px-1 py-1.5 text-right font-medium">7G</th><th className="px-1 py-1.5 text-right font-medium">30G</th><th className="px-4 py-1.5 text-right font-medium">90G</th></tr></thead>
                <tbody>{perf.data.by_type.map((p) => <tr key={p.signal_type} className="border-b border-border/40 last:border-0"><td className="px-4 py-1.5">{p.signal_type.replaceAll("_", " ").toLowerCase()}</td><td className="num px-1 py-1.5 text-right text-muted-foreground">{p.count}</td><Ret v={p.avg_ret_7d} /><Ret v={p.avg_ret_30d} /><Ret v={p.avg_ret_90d} last /></tr>)}</tbody>
              </table>
            </Section>
          )}
          <Section title={market === "TR" ? "Canlı KAP" : "Latest filings"} hint="son bildirimler" right={<Link to="/live" className="text-xs text-primary hover:underline">Tümü →</Link>}>
            {events.data?.map((ev) => <EventRow key={ev.id} ev={ev} compact />)}
          </Section>
        </div>
      </div>
    </div>
  )
}

function Ret({ v, last }: { v: number | null; last?: boolean }) {
  return <td className={cn("num py-1.5 text-right", last ? "px-4" : "px-1", v === null ? "text-muted-foreground" : v < 0 ? "text-negative" : "text-positive")}>{v === null ? "—" : `${v > 0 ? "+" : ""}${v.toFixed(1)}%`}</td>
}

function FlowTable({ rows, market, negative }: { rows: RadarRow[]; market: string; negative?: boolean }) {
  if (rows.length === 0) return <div className="px-4 py-6 text-sm text-muted-foreground">Veri yok.</div>
  const max = Math.max(...rows.map((x) => Math.abs(x.net_flow_value)))
  return (
    <table className="w-full text-sm">
      <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
        <tr className="border-b border-border/60">
          <th className="px-4 py-2 text-left font-medium">Hisse</th>
          <th className="px-2 py-2 text-right font-medium">Net akış</th>
          <th className="hidden px-2 py-2 text-right font-medium sm:table-cell">Fon</th>
          <th className="px-2 py-2 text-right font-medium">Smart Money</th>
          <th className="hidden px-4 py-2 text-right font-medium md:table-cell">Konsensüs</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((x) => (
          <tr key={x.symbol} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
            <td className="px-4 py-2.5">
              <Link to={`/stocks/${x.symbol}`} className="font-semibold hover:underline">{x.symbol}</Link>
              <div className="relative mt-1 h-1 w-24 overflow-hidden rounded-full bg-muted">
                <div className={cn("h-full", negative ? "bg-negative/70" : "bg-positive/70")} style={{ width: `${(Math.abs(x.net_flow_value) / max) * 100}%` }} />
              </div>
            </td>
            <td className="px-2 py-2.5 text-right"><Flow value={x.net_flow_value} market={market} /></td>
            <td className="hidden px-2 py-2.5 text-right sm:table-cell">
              <span className={cn("num", negative ? "text-negative" : "text-positive")}>{negative ? `${x.funds_reducing} ↓` : `${x.funds_increasing} ↑`}</span>
              {x.funds_new > 0 && <span className="ml-1 text-[10px] text-positive">+{x.funds_new} yeni</span>}
              {x.funds_exited > 0 && <span className="ml-1 text-[10px] text-negative">{x.funds_exited} çıkış</span>}
            </td>
            <td className="px-2 py-2.5 text-right"><ScorePill value={x.smart_money_score ?? null} /></td>
            <td className="hidden px-4 py-2.5 text-right md:table-cell"><ScorePill value={x.consensus_score ?? null} size="sm" /></td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Empty({ market }: { market: string }) {
  return (
    <div className="rounded-lg border border-dashed border-border p-10 text-center">
      <div className="text-lg font-semibold">Henüz veri yok</div>
      <p className="mt-1 text-sm text-muted-foreground">İlk veri çekimi yapılmadı{market === "US" ? " (SEC 13F)" : " (KAP)"}. Admin isen aşağıdaki butonla başlat; değilsen yöneticiye haber ver.</p>
      <PipelineButton size="default" className="mt-4 justify-center" />
    </div>
  )
}

function RadarSkeleton() {
  return (
    <div className="space-y-5">
      <Skeleton className="h-10 w-72" />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">{[0, 1, 2, 3].map((i) => <Skeleton key={i} className="h-20" />)}</div>
      <Skeleton className="h-80" />
    </div>
  )
}
