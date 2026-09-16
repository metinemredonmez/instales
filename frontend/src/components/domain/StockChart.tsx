import { useQuery } from "@tanstack/react-query"
import { Area, AreaChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { api, type Market } from "@/lib/api"
import { fmtDate, fmtLots } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { useI18n } from "@/lib/i18n"

type Point = { t: string; price: number | null; qty: number | null; funds: number | null; snap: boolean }

/**
 * One row per calendar date across both series so the two panels share the same x domain: the crosshair
 * lands on the same date in each, not on the same array index. Prices are daily; holdings are report
 * snapshots carried forward (a position holds until the next report) and flagged `snap` on report dates.
 */
export function mergeSeries(prices: { date: string; close: number }[], holdings: { date: string; quantity: number; funds: number }[]): Point[] {
  const dates = [...new Set([...prices.map((p) => p.date), ...holdings.map((h) => h.date)])].sort()
  const priceAt = new Map(prices.map((p) => [p.date, p.close]))
  const holdAt = new Map(holdings.map((h) => [h.date, h]))
  let last: { quantity: number; funds: number } | null = null
  return dates.map((d) => {
    const h = holdAt.get(d)
    if (h) last = h
    return { t: d, price: priceAt.get(d) ?? null, qty: last?.quantity ?? null, funds: last?.funds ?? null, snap: h !== undefined }
  })
}

/**
 * Price and aggregate institutional holdings on a shared time axis — as two stacked panels with a
 * synced crosshair, never a dual-axis chart. Each panel is a single series, so the title is the legend.
 */
export function StockChart({ symbol, market }: { symbol: string; market: Market }) {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["series", market, symbol], queryFn: () => api.series(market, symbol) })
  if (!q.data || (q.data.prices.length === 0 && q.data.holdings.length === 0)) return null
  const { prices, holdings } = q.data
  const data = mergeSeries(prices, holdings)
  const priceChange = prices.length > 1 ? ((prices.at(-1)!.close / prices[0].close) - 1) * 100 : null
  const holdChange = holdings.length > 1 ? ((holdings.at(-1)!.quantity / holdings[0].quantity) - 1) * 100 : null

  return (
    <Section title={t("chart.title")} hint={t("chart.hint")}>
      <div className="grid gap-0 px-2 py-3">
        <Panel title={t("chart.price")} change={priceChange} unit={market === "US" ? "$" : "₺"}>
          <ResponsiveContainer width="100%" height={150}>
            <LineChart data={data} syncId={symbol} syncMethod="value" margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 4" />
              <XAxis dataKey="t" hide />
              <YAxis width={48} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} domain={["auto", "auto"]} />
              <Tooltip content={<Tip label={t("chart.close")} fmt={(v) => `${market === "US" ? "$" : "₺"}${v}`} />} cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }} />
              <Line type="monotone" dataKey="price" connectNulls stroke="var(--primary)" strokeWidth={2} dot={false} activeDot={{ r: 4, strokeWidth: 2, stroke: "var(--card)" }} isAnimationActive={false} />
            </LineChart>
          </ResponsiveContainer>
        </Panel>
        <Panel title={t("chart.holdings")} change={holdChange} unit="">
          <ResponsiveContainer width="100%" height={150}>
            <AreaChart data={data} syncId={symbol} syncMethod="value" margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
              <CartesianGrid vertical={false} stroke="var(--border)" strokeDasharray="2 4" />
              <XAxis dataKey="t" tickFormatter={(d) => fmtDate(d)} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
              <YAxis width={48} tickFormatter={(v) => fmtLots(v).replace("+", "")} tick={{ fontSize: 10, fill: "var(--muted-foreground)" }} axisLine={false} tickLine={false} />
              <Tooltip content={<Tip label={t("common.lot")} fmt={(v, p) => `${fmtLots(v).replace("+", "")} · ${p?.funds ?? "?"} ${t("common.fund").toLowerCase()}`} />} cursor={{ stroke: "var(--muted-foreground)", strokeDasharray: "3 3" }} />
              {/* dots only on report dates; the carried-forward days are the step, not observations */}
              <Area type="stepAfter" dataKey="qty" connectNulls stroke="var(--positive)" strokeWidth={2} fill="var(--positive)" fillOpacity={0.12} dot={(p) => <SnapDot key={String(p.key ?? p.index)} cx={p.cx} cy={p.cy} payload={p.payload} />} activeDot={{ r: 5, strokeWidth: 2, stroke: "var(--card)" }} isAnimationActive={false} />
            </AreaChart>
          </ResponsiveContainer>
        </Panel>
      </div>
    </Section>
  )
}

function SnapDot(props: { cx?: number; cy?: number; payload?: unknown }) {
  const { cx, cy } = props
  const p = props.payload as Point | undefined
  if (!p?.snap || cx === undefined || cy === undefined) return <g />
  return <circle cx={cx} cy={cy} r={3} fill="var(--positive)" strokeWidth={0} />
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

function Tip({ active, payload, label, fmt }: { active?: boolean; payload?: { value: number | null; payload: { funds?: number | null } }[]; label?: string; fmt: (v: number, p?: { funds?: number | null }) => string; }) {
  if (!active || !payload?.length || payload[0].value === null || payload[0].value === undefined) return null
  return (
    <div className="rounded-md border border-border bg-popover px-2.5 py-1.5 text-xs shadow-md">
      <div className="text-muted-foreground">{fmtDate(String(label))}</div>
      <div className="num font-medium">{fmt(payload[0].value, payload[0].payload)}</div>
    </div>
  )
}
