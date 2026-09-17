import { useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, type BalanceKey, type CashflowKey, type Derived, type Fundamentals as FundamentalsData, type FundamentalsPeriod, type FundamentalsSnapshot, type FundamentalsSummary, type IncomeKey, type Market, type Statement, type StatementKey, type StatementKind } from "@/lib/api"
import { fmtCompact, fmtDate, fmtDateTime, fmtNum, fmtPeriod, fmtPrice } from "@/lib/format"
import { useI18n, type T } from "@/lib/i18n"
import { providerLabel } from "@/lib/quotes"
import { Section } from "@/components/layout/Section"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const PERIODS: FundamentalsPeriod[] = ["annual", "quarterly"]
const KINDS: StatementKind[] = ["income", "balance", "cashflow"]
/** Row order per statement — the contract's canonical keys, top line first. Every row is shown; a line the filing lacks is a dash. */
const ROWS: { income: IncomeKey[]; balance: BalanceKey[]; cashflow: CashflowKey[] } = {
  income: ["revenue", "cost_of_revenue", "gross_profit", "operating_income", "ebitda", "pretax_income", "net_income", "eps_diluted", "interest_expense"],
  balance: ["total_assets", "total_liabilities", "equity", "total_debt", "cash", "current_assets", "current_liabilities"],
  cashflow: ["operating_cf", "capex", "free_cf", "dividends_paid", "share_repurchase"],
}
const MAX_COLUMNS = 5
/** Statement lines whose sign carries meaning: a loss, negative equity or a cash burn is red. Capex, dividends paid and
 *  buybacks are outflows Yahoo prints negative on every filer — red there would mean nothing. */
const SIGNED = new Set<StatementKey>(["gross_profit", "operating_income", "ebitda", "pretax_income", "net_income", "eps_diluted", "equity", "operating_cf", "free_cf"])

/** Percentages arrive ×100 already; `signed` prefixes "+" on the growth chips — a change, not a level. */
const pct = (v: number | null | undefined, signed = false) => (v === null || v === undefined ? "—" : `${fmtNum(v, 1, signed)}%`)

/**
 * Yahoo fundamentals for one stock: valuation snapshot, the derived-ratio strip, then the reported statements with an
 * annual/quarterly toggle. Everything on screen is a number the API sent or a dash. The section hides itself when
 * nothing has ever loaded, instead of rendering a broken box, because fundamentals are context here — the flows
 * above are the product; once a period has loaded it stays on screen, and a period that then fails to load says so
 * in one line rather than taking the section (and the way back) with it.
 */
export function Fundamentals({ symbol, market }: { symbol: string; market: Market }) {
  const { t } = useI18n()
  const [period, setPeriod] = useState<FundamentalsPeriod>("annual")
  const q = useQuery({ queryKey: ["fundamentals", market, symbol, period], queryFn: () => api.fundamentals(market, symbol, period), placeholderData: (prev) => prev })
  const last = useRef<FundamentalsData>(undefined)
  if (q.data) last.current = q.data
  const d = q.data ?? last.current

  if (!d) return q.isError ? null : <Skeleton className="h-64" />
  const hasStatements = KINDS.some((k) => d.statements[k].length > 0)
  const empty = !d.snapshot && !hasStatements

  return (
    <Section
      title={t("fin.title")}
      hint={t("fin.hint")}
      right={empty ? undefined : <Segmented value={period} options={PERIODS.map((p) => ({ value: p, label: t(`fin.period.${p}`) }))} onChange={setPeriod} />}
      className={cn(q.isPlaceholderData && "opacity-60 transition-opacity")}
    >
      {q.isError && <div className="px-4 py-2 text-xs text-negative">{t("fin.error")}</div>}
      {empty ? (
        <div className="px-4 py-6 text-sm text-muted-foreground">{t("fin.empty")}</div>
      ) : (
        <div className="divide-y divide-border/60">
          {d.snapshot && <MetricsGrid snap={d.snapshot} />}
          <DerivedStrip derived={d.derived} />
          <Statements data={d} />
          <Footnote data={d} t={t} />
        </div>
      )}
    </Section>
  )
}

/**
 * Header chips under the stock name: market cap · P/E · P/B · net margin. Only figures Yahoo stated become chips;
 * with none (statements fetched, no snapshot yet) nothing is rendered — never a row of dashes. The chips sit in the
 * StockPage's header row next to the insiders chip, which hides itself when it ends up empty.
 */
export function FundamentalsChips({ f }: { f: FundamentalsSummary }) {
  const { t } = useI18n()
  const chips: [string, string][] = [
    [t("fin.m.marketCap"), fmtCompact(f.market_cap, f.quote_currency)],
    [t("fin.m.pe"), fmtNum(f.pe, 1)],
    [t("fin.m.pb"), fmtNum(f.price_to_book, 1)],
    [t("fin.d.netMargin"), pct(f.net_margin)],
  ]
  const present = chips.filter(([, v]) => v !== "—")
  if (present.length === 0) return null
  return (
    <>
      {present.map(([k, v]) => (
        <span key={k} className="inline-flex items-center gap-1 rounded-sm border border-border px-1.5 py-0.5">
          <span className="text-muted-foreground">{k}</span>
          <span className="num font-medium">{v}</span>
        </span>
      ))}
    </>
  )
}

/** Market cap, EV and the 52-week range are listing-currency figures (`quote_currency`); the statements below are in the reporting currency. */
function MetricsGrid({ snap }: { snap: FundamentalsSnapshot }) {
  const { t } = useI18n()
  const currency = snap.quote_currency
  const price = (v: number | null) => fmtPrice(v, currency)
  const range = snap.week52_low === null && snap.week52_high === null ? "—" : `${price(snap.week52_low)} – ${price(snap.week52_high)}`
  const cells: { label: string; value: string }[] = [
    { label: t("fin.m.marketCap"), value: fmtCompact(snap.market_cap, currency) },
    { label: t("fin.m.enterpriseValue"), value: fmtCompact(snap.enterprise_value, currency) },
    { label: t("fin.m.pe"), value: fmtNum(snap.pe, 1) },
    { label: t("fin.m.forwardPe"), value: fmtNum(snap.forward_pe, 1) },
    { label: t("fin.m.pb"), value: fmtNum(snap.price_to_book, 1) },
    { label: t("fin.m.evEbitda"), value: fmtNum(snap.ev_to_ebitda, 1) },
    { label: t("fin.m.netMargin"), value: pct(snap.profit_margin) },
    { label: t("fin.m.roe"), value: pct(snap.return_on_equity) },
    { label: t("fin.m.dividendYield"), value: pct(snap.dividend_yield) },
    { label: t("fin.m.beta"), value: fmtNum(snap.beta, 2) },
    { label: t("fin.m.range52w"), value: range },
    { label: t("fin.m.shares"), value: fmtCompact(snap.shares_outstanding) },
  ]
  return (
    <div className="px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{t("fin.valuation")} · {fmtDate(snap.as_of)}</div>
      <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-2 text-sm sm:grid-cols-3 lg:grid-cols-6">
        {cells.map((c) => (
          <div key={c.label}>
            <dt className="text-xs text-muted-foreground">{c.label}</dt>
            <dd className="num font-medium">{c.value}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

/**
 * Margins and growth are coloured by sign — a negative margin is a loss, red; only the two growth chips are changes
 * and get a "+" (a margin is a level, the header chip prints it unsigned too). Leverage stays neutral. Nothing derived → no strip.
 */
function DerivedStrip({ derived }: { derived: Derived }) {
  const { t } = useI18n()
  const items: { label: string; value: number | null; neutral?: boolean; growth?: boolean }[] = [
    { label: t("fin.d.grossMargin"), value: derived.gross_margin },
    { label: t("fin.d.operatingMargin"), value: derived.operating_margin },
    { label: t("fin.d.netMargin"), value: derived.net_margin },
    { label: t("fin.d.fcfMargin"), value: derived.fcf_margin },
    { label: t("fin.d.debtToEquity"), value: derived.debt_to_equity, neutral: true },
    { label: t("fin.d.revenueYoy"), value: derived.revenue_growth_yoy, growth: true },
    { label: t("fin.d.netIncomeYoy"), value: derived.net_income_growth_yoy, growth: true },
  ]
  if (items.every((it) => it.value === null)) return null
  return (
    <div className="px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
        {t("fin.d.title")}{derived.period_end && <> · {t("fin.d.hint", { p: fmtPeriod(derived.period_end) })}</>}
      </div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
        {items.map((it) => (
          <span key={it.label} className="inline-flex items-center gap-1.5 rounded-sm border border-border px-2 py-1">
            <span className="text-muted-foreground">{it.label}</span>
            <span className={cn("num font-medium", !it.neutral && it.value !== null && it.value > 0 && "text-positive", !it.neutral && it.value !== null && it.value < 0 && "text-negative")}>
              {pct(it.value, !!it.growth)}
            </span>
          </span>
        ))}
      </div>
    </div>
  )
}

function Statements({ data }: { data: FundamentalsData }) {
  const { t } = useI18n()
  const [kind, setKind] = useState<StatementKind>("income")
  const rows = data.statements[kind]
  return (
    <div className="px-4 py-3">
      <Segmented value={kind} options={KINDS.map((k) => ({ value: k, label: t(`fin.stmt.${k}`) }))} onChange={setKind} />
      {rows.length === 0 ? (
        <div className="py-4 text-sm text-muted-foreground">{t("fin.stmt.none")}</div>
      ) : (
        <StatementTable rows={rows} keys={ROWS[kind]} currency={data.currency} t={t} />
      )}
    </div>
  )
}

/** Newest period first, at most five columns. Diluted EPS is a per-share figure and keeps its decimals; every other line is money in the reporting currency. */
function StatementTable({ rows, keys, currency, t }: { rows: Statement[]; keys: StatementKey[]; currency: string; t: T }) {
  const cols = rows.slice(0, MAX_COLUMNS)
  const cell = (key: StatementKey, v: number | null | undefined) => (v === null || v === undefined ? "—" : key === "eps_diluted" ? fmtNum(v, 2) : fmtCompact(v, currency))
  return (
    <div className="mt-2 overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
          <tr className="border-b border-border/60">
            <th className="py-2 pr-3 text-left font-medium">{currency}</th>
            {cols.map((c) => <th key={c.period_end} className="num px-2 py-2 text-right font-medium">{fmtPeriod(c.period_end)}</th>)}
          </tr>
        </thead>
        <tbody>
          {keys.map((k) => (
            <tr key={k} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
              <td className="whitespace-nowrap py-1.5 pr-3 text-muted-foreground">{t(`fin.k.${k}`)}</td>
              {cols.map((c) => {
                const v = c.items[k]
                return <td key={c.period_end} className={cn("num whitespace-nowrap px-2 py-1.5 text-right", SIGNED.has(k) && typeof v === "number" && v < 0 && "text-negative")}>{cell(k, v)}</td>
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

/** "Kaynak: Yahoo Finance · 16 Eyl 14:05 · rakamlar raporlandığı gibi, tavsiye değildir" — provenance travels with the numbers. */
function Footnote({ data, t }: { data: FundamentalsData; t: T }) {
  const parts = [t("quotes.source", { src: providerLabel(data.source, t) }), data.fetched_at ? fmtDateTime(data.fetched_at) : null, t("fin.disclaimer")]
  return <div className="px-4 py-2 text-[11px] text-muted-foreground">{parts.filter(Boolean).join(" · ")}</div>
}

function Segmented<V extends string>({ value, options, onChange }: { value: V; options: { value: V; label: string }[]; onChange: (v: V) => void }) {
  return (
    <div className="inline-flex rounded-md border border-border p-0.5 text-xs">
      {options.map((o) => (
        <button key={o.value} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)} className={cn("rounded-[5px] px-3 py-1 font-medium transition", o.value === value ? "bg-secondary" : "text-muted-foreground hover:text-foreground")}>
          {o.label}
        </button>
      ))}
    </div>
  )
}
