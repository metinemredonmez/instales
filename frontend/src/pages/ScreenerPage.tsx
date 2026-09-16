import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api, type ScreenerFilters, type SignalType } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { fmtPct } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { Flow, ScorePill, SIGNAL_LABEL, SignalBadge } from "@/components/domain/badges"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const PRESETS: { label: string; f: ScreenerFilters }[] = [
  { label: "Fiyat düşerken fonlar alıyor", f: { min_funds_increasing: 3, max_price_change_30d_pct: 0 } },
  { label: "Yeni pozisyon kümesi", f: { signal: ["NEW_POSITION_CLUSTER"] } },
  { label: "Güçlü konsensüs", f: { min_consensus_score: 75, min_funds_increasing: 3 } },
  { label: "Dağıtım / çıkış", f: { signal: ["DISTRIBUTION", "EXIT_CLUSTER"] } },
]

export function ScreenerPage() {
  const { market } = useMarket()
  const [f, setF] = useState<ScreenerFilters>({})
  const q = useQuery({ queryKey: ["screener", market, f], queryFn: () => api.screener(market, f) })
  const num = (k: keyof ScreenerFilters) => (e: React.ChangeEvent<HTMLInputElement>) =>
    setF({ ...f, [k]: e.target.value === "" ? undefined : Number(e.target.value) })
  const toggleSignal = (s: SignalType) => {
    const cur = new Set(f.signal ?? [])
    cur.has(s) ? cur.delete(s) : cur.add(s)
    setF({ ...f, signal: cur.size ? [...cur] : undefined })
  }

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Smart Money Screener</h1>
        <p className="text-sm text-muted-foreground">Filtreler AND ile birleşir. Skorlar son hesaplama tarihine aittir.</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <Button key={p.label} variant="outline" size="sm" onClick={() => setF(p.f)}>{p.label}</Button>
        ))}
        <Button variant="ghost" size="sm" onClick={() => setF({})}>Temizle</Button>
      </div>
      <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
        <div className="space-y-3 rounded-lg border border-border bg-card p-4 text-sm">
          <Field label="Smart Money ≥" value={f.min_smart_money_score} onChange={num("min_smart_money_score")} />
          <Field label="Konsensüs ≥" value={f.min_consensus_score} onChange={num("min_consensus_score")} />
          <Field label="Artıran fon ≥" value={f.min_funds_increasing} onChange={num("min_funds_increasing")} />
          <Field label="Yeni pozisyon ≥" value={f.min_funds_new} onChange={num("min_funds_new")} />
          <Field label="Net akış ≥ (₺)" value={f.min_net_flow_value} onChange={num("min_net_flow_value")} />
          <Field label="30G fiyat değişimi ≤ (%)" value={f.max_price_change_30d_pct} onChange={num("max_price_change_30d_pct")} />
          <div>
            <div className="mb-1.5 text-xs text-muted-foreground">Sinyal (en az biri)</div>
            <div className="flex flex-wrap gap-1.5">
              {(Object.keys(SIGNAL_LABEL) as SignalType[]).map((s) => (
                <button key={s} onClick={() => toggleSignal(s)} className={cn("rounded-sm border px-1.5 py-0.5 text-[11px]", f.signal?.includes(s) ? "border-primary bg-primary/15 text-foreground" : "border-border text-muted-foreground")}>
                  {SIGNAL_LABEL[s].label}
                </button>
              ))}
            </div>
          </div>
        </div>
        <Section title="Sonuçlar" hint={`${q.data?.length ?? 0}`}>
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
              <tr className="border-b border-border/60">
                <th className="px-4 py-2 text-left font-medium">Hisse</th>
                <th className="px-2 py-2 text-right font-medium">Smart Money</th>
                <th className="px-2 py-2 text-right font-medium">Konsensüs</th>
                <th className="px-2 py-2 text-right font-medium">Fon ↑/↓</th>
                <th className="px-2 py-2 text-right font-medium">Net akış</th>
                <th className="px-2 py-2 text-right font-medium">30G fiyat</th>
                <th className="px-4 py-2 text-left font-medium">Sinyaller</th>
              </tr>
            </thead>
            <tbody>
              {q.data?.map((r) => (
                <tr key={r.symbol} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                  <td className="px-4 py-2"><Link to={`/stocks/${r.symbol}`} className="font-semibold hover:underline">{r.symbol}</Link></td>
                  <td className="px-2 py-2 text-right"><ScorePill value={r.smart_money_score} /></td>
                  <td className="px-2 py-2 text-right"><ScorePill value={r.consensus_score} size="sm" /></td>
                  <td className="num px-2 py-2 text-right"><span className="text-positive">{r.funds_increasing}</span> / <span className="text-negative">{r.funds_reducing}</span></td>
                  <td className="px-2 py-2 text-right"><Flow value={r.net_flow_value} market={market} /></td>
                  <td className={cn("num px-2 py-2 text-right", (r.price_change_30d_pct ?? 0) < 0 ? "text-negative" : "text-positive")}>{fmtPct(r.price_change_30d_pct)}</td>
                  <td className="px-4 py-2"><div className="flex flex-wrap gap-1">{r.signals.map((s) => <SignalBadge key={s} type={s} />)}</div></td>
                </tr>
              ))}
              {q.data?.length === 0 && <tr><td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">Filtreye uyan hisse yok.</td></tr>}
            </tbody>
          </table>
        </Section>
      </div>
    </div>
  )
}

function Field({ label, value, onChange }: { label: string; value: number | undefined; onChange: (e: React.ChangeEvent<HTMLInputElement>) => void }) {
  return (
    <label className="block">
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <input type="number" value={value ?? ""} onChange={onChange} className="num h-8 w-full rounded-md border border-input bg-background px-2 text-sm outline-none focus:ring-2 focus:ring-ring/40" />
    </label>
  )
}
