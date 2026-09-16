import { useQuery } from "@tanstack/react-query"
import { Link, useParams } from "react-router-dom"
import { api } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { fmtDate, fmtLots } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { Flow } from "@/components/domain/badges"
import { EventRow } from "@/components/domain/EventRow"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

export function InstitutionsPage() {
  const { market } = useMarket()
  const q = useQuery({ queryKey: ["institutions", market], queryFn: () => api.institutions(market) })
  return (
    <div className="space-y-5">
      <div><h1 className="text-2xl font-semibold tracking-tight">{market === "TR" ? "Portföy şirketleri" : "Institutions"}</h1><p className="text-sm text-muted-foreground">Bildirimi yapan kurum; altındaki fonların toplam hareketi.</p></div>
      <Section title="Kurumlar" hint={`${q.data?.length ?? 0}`}>
        <table className="w-full text-sm">
          <thead className="text-[11px] uppercase tracking-wider text-muted-foreground"><tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">Kurum</th><th className="px-2 py-2 text-right font-medium">Fon</th><th className="px-2 py-2 text-right font-medium">Bildirim</th><th className="px-4 py-2 text-right font-medium">Durum</th></tr></thead>
          <tbody>
            {q.data?.map((i) => (
              <tr key={i.code} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                <td className="px-4 py-2.5"><Link to={`/institutions/${i.code}`} className="font-semibold hover:underline">{i.name}</Link><div className="text-xs text-muted-foreground">{i.kind.replaceAll("_", " ").toLowerCase()}</div></td>
                <td className="num px-2 py-2.5 text-right">{i.funds}</td>
                <td className="num px-2 py-2.5 text-right">{i.events}</td>
                <td className="px-4 py-2.5 text-right text-xs">{i.is_verified ? <span className="text-positive">doğrulandı</span> : <span className="text-warning">inceleme bekliyor</span>}</td>
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
  const q = useQuery({ queryKey: ["institution", market, code], queryFn: () => api.institution(market, code) })
  if (q.isLoading) return <Skeleton className="h-96" />
  if (!q.data) return <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">Kurum bulunamadı.</div>
  const d = q.data
  return (
    <div className="space-y-5">
      <div>
        <div className="text-xs text-muted-foreground">{d.kind.replaceAll("_", " ").toLowerCase()} · aktivite dönemi {fmtDate(d.activity_period_end)}</div>
        <h1 className="mt-1 text-3xl font-semibold tracking-tight">{d.name}</h1>
        <div className="mt-2 flex flex-wrap gap-1.5">{d.funds.map((f) => <Link key={f.code} to={`/funds/${f.code}`} className="rounded bg-secondary px-2 py-0.5 font-mono text-xs hover:underline" title={f.name}>{f.code}</Link>)}</div>
      </div>
      <div className="grid gap-5 lg:grid-cols-2">
        <AggTable title="Fonlarının en çok artırdığı" rows={d.top_increased} market={market} />
        <AggTable title="Fonlarının en çok azalttığı" rows={d.top_reduced} market={market} negative />
      </div>
      <Section title="KAP bildirimleri" hint={`${d.events.length}`}>{d.events.map((ev) => <EventRow key={ev.id} ev={ev} />)}{d.events.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">Yok.</div>}</Section>
    </div>
  )
}

function AggTable({ title, rows, market, negative }: { title: string; rows: { symbol: string; delta_qty: number; delta_value: number; funds_increasing: number; funds_reducing: number }[]; market: string; negative?: boolean }) {
  return (
    <Section title={title} hint="kurum toplamı">
      {rows.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">Yok.</div>}
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
