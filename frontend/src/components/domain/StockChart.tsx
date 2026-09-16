import { useQuery } from "@tanstack/react-query"
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { api, type Market } from "@/lib/api"
import { fmtDate, fmtLots } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { useI18n } from "@/lib/i18n"

/**
 * Price and aggregate institutional holdings on a shared time axis — as two stacked panels with a
 * synced crosshair, never a dual-axis chart. Each panel is a single series, so the title is the legend.
 */
export function StockChart({ symbol, market }: { symbol: string; market: Market }) {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["series", market, symbol], queryFn: () => api.series(market, symbol) })
  if (!q.data || (q.data.prices.length === 0 && q.data.holdings.length === 0)) return null
  const prices = q.data.prices.map((p) => ({ t: p.date, v: p.close }))
  const holdings = q.data.holdings.map((h) => ({ t: h.date, v: h.quantity, funds: h.funds }))
  const priceChange = prices.length > 1 ? ((prices.at(-1)!.v / prices[0].v) - 1) * 100 : null
  const holdChange = holdings.length > 1 ? ((holdings.at(-1)!.v / holdings[0].v) - 1) * 100 : null

  return (
    <Section title={t("chart.title")} hint={t("chart.hint")}>
      <div className="grid gap-0 px-2 py-3">
        <Panel title={t("chart.price")} change={priceChange} unit={market === "US" ? "$" : "₺"}>
          <ResponsiveContainer width="100%" height={150}>
            <LineChart data={prices} syncId={symbol} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 4" />
              <XAxis dataKey="t" hide />
              <YAxis width={48} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
              <Tooltip content={<Tip label={t("chart.close")} fmt={(v) => `${market === "US" ? "$" : "₺"}${v}`} />} cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }} />
              <Line type="monotone" dataKey="v" stroke="var(--primary)" strokeWidth={2} dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--card)" }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Panel>
        <Panel title={t("chart.holdings")} change={holdChange} unit="">
          <ResponsiveContainer width="100%" height={150}>
            <AreaChart data={holdings} syncId={symbol} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 4" />
              <XAxis dataKey="t" tickFormatter={(d) => fmtDate(d)} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
              <YAxis width={48} tickFormatter={(v) => fmtLots(v).replace("+", "")} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
              <Tooltip content={<Tip label={t("common.lot")} fmt={(v, p) => `${fmtLots(v).replace("+", "")} · ${p?.funds ?? "?"} ${t("common.fund").toLowerCase()}`} />} cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }} />
              <Area type="stepAfter" dataKey="v" stroke="var(--positive)" strokeWidth={2} fill="var(--positive)" fillOpacity={0.12} dot={{ r: 3, fill: "var(--positive)", strokeWidth: 0 }} activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--card)" }} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>
      </div>
    </Section>
  )
}

function Panel({ title, change, unit, children }: { title: string; change: number | null; unit: string; children: React.ReactNode }) {
  const { t } = useI18n()
  return (
    <div>
      <div className="flex items-baseline gap-2 px-2 text-xs">
        <span className="font-medium">{title}</span>
        {change !== null && <span className={`num ${change < 0 ? "text-negative" : "text-positive"}`}>{change > 0 ? "+" : ""}{change.toFixed(1)}%{unit && ""}</span>}
        <span className="text-muted-foreground">{t("chart.sincePeriodStart")}</span>
      </div>
      {children}
    </div>
  )
}

function Tip({ active, payload, label, fmt }: { active?: boolean; payload?: { value: number; payload: { funds?: number } }[]; label?: string; fmt: (v: number, p?: { funds?: number }) => string; }) {
  if (!active || !payload?.length) return null
  return (
    <div className="rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs shadow-md">
      <div className="text-muted-foreground">{fmtDate(String(label))}</div>
      <div className="num font-medium">{fmt(payload[0].value, payload[0].payload)}</div>
    </div>
  )
}
