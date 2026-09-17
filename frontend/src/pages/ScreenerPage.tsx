import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api, type ScreenerFilters, type SignalType } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n } from "@/lib/i18n"
import { fmtPct } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { Flow, ScorePill, SIGNAL_TYPES, SignalBadge, signalLabel } from "@/components/domain/badges"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"

const PRESETS: { key: "screener.preset.dip" | "screener.preset.cluster" | "screener.preset.consensus" | "screener.preset.distribution"; f: ScreenerFilters }[] = [
  { key: "screener.preset.dip", f: { min_funds_increasing: 3, max_price_change_30d_pct: 0 } },
  { key: "screener.preset.cluster", f: { signal: ["NEW_POSITION_CLUSTER"] } },
  { key: "screener.preset.consensus", f: { min_consensus_score: 75, min_funds_increasing: 3 } },
  { key: "screener.preset.distribution", f: { signal: ["DISTRIBUTION", "EXIT_CLUSTER"] } },
]

export function ScreenerPage() {
  const { market } = useMarket()
  const { t } = useI18n()
  const [f, setF] = useState<ScreenerFilters>({})
  const q = useQuery({ queryKey: ["screener", market, f], queryFn: () => api.screener(market, f), refetchInterval: 60_000, placeholderData: (prev) => prev })
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
        <p className="text-sm text-muted-foreground">{t("screener.sub")}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {PRESETS.map((p) => (
          <Button key={p.key} variant="outline" size="sm" onClick={() => setF(p.f)}>{t(p.key)}</Button>
        ))}
        <Button variant="ghost" size="sm" onClick={() => setF({})}>{t("common.clear")}</Button>
      </div>
      <div className="grid gap-5 lg:grid-cols-[280px_1fr]">
        <div className="space-y-3 rounded-lg border border-border bg-card p-4 text-sm">
          <Field label="Smart Money ≥" value={f.min_smart_money_score} onChange={num("min_smart_money_score")} />
          <Field label={`${t("common.consensus")} ≥`} value={f.min_consensus_score} onChange={num("min_consensus_score")} />
          <Field label={`${t("screener.fundsIncreasing")} ≥`} value={f.min_funds_increasing} onChange={num("min_funds_increasing")} />
          <Field label={`${t("common.newPosition")} ≥`} value={f.min_funds_new} onChange={num("min_funds_new")} />
          <Field label={`${t("common.netFlow")} ≥ (${market === "US" ? "$" : "₺"})`} value={f.min_net_flow_value} onChange={num("min_net_flow_value")} />
          {/* The API's price change is a fixed 30 calendar days for both markets (not the 100D US score window) — the label says 30D on purpose. */}
          <Field label={`${t("screener.priceChange30")} ≤ (%)`} title={t("screener.price30.hint")} value={f.max_price_change_30d_pct} onChange={num("max_price_change_30d_pct")} />
          <div>
            <div className="mb-1.5 text-xs text-muted-foreground">{t("screener.signalAny")}</div>
            <div className="flex flex-wrap gap-1.5">
              {SIGNAL_TYPES.map((s) => (
                <button key={s} onClick={() => toggleSignal(s)} className={cn("rounded-sm border px-1.5 py-0.5 text-[11px]", f.signal?.includes(s) ? "border-primary bg-primary/15 text-foreground" : "border-border text-muted-foreground")}>
                  {signalLabel(t, s)}
                </button>
              ))}
            </div>
          </div>
        </div>
        <Section title={t("screener.results")} hint={`${q.data?.length ?? 0}`}>
          <table className="w-full text-sm">
            <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
              <tr className="border-b border-border/60">
                <th className="px-4 py-2 text-left font-medium">{t("common.stock")}</th>
                <th className="px-2 py-2 text-right font-medium">Smart Money</th>
                <th className="px-2 py-2 text-right font-medium">{t("common.consensus")}</th>
                <th className="px-2 py-2 text-right font-medium">{t("common.fund")} ↑/↓</th>
                <th className="px-2 py-2 text-right font-medium">{t("common.netFlow")}</th>
                <th className="px-2 py-2 text-right font-medium" title={t("screener.price30.hint")}>{t("screener.price30")}</th>
                <th className="px-4 py-2 text-left font-medium">{t("common.signals")}</th>
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
              {q.data?.length === 0 && <tr><td colSpan={7} className="px-4 py-8 text-center text-sm text-muted-foreground">{t("screener.empty")}</td></tr>}
            </tbody>
          </table>
        </Section>
      </div>
    </div>
  )
}

function Field({ label, title, value, onChange }: { label: string; title?: string; value: number | undefined; onChange: (e: React.ChangeEvent<HTMLInputElement>) => void }) {
  return (
    <label className="block" title={title}>
      <div className="mb-1 text-xs text-muted-foreground">{label}</div>
      <input type="number" value={value ?? ""} onChange={onChange} className="num h-8 w-full rounded-md border border-input bg-background px-2 text-sm outline-none focus:ring-2 focus:ring-ring/40" />
    </label>
  )
}
