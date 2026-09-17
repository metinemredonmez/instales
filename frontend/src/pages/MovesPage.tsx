import { useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { api, type Market, type MoveKind, type MoveParty, type MoveRow } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n } from "@/lib/i18n"
import { fmtDate, fmtLots, fmtMoney } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { ACT, ConfidenceBadge, Flow } from "@/components/domain/badges"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const KINDS: MoveKind[] = ["buys", "sells", "new", "exits"]
type Window = 7 | 30 | 90 | 100 | 180 | 365
// Window steps per market, the same ones Radar offers: KAP is daily, 13F is quarterly (100D ≈ the latest quarter, 7D is noise).
const WINDOWS: Record<Market, readonly Window[]> = { TR: [7, 30, 90, 365], US: [100, 180, 365] }
// The market's own score window (pipeline.MARKET_WINDOW_DAYS), so the default ranking matches Radar and the AI tools.
const DEFAULT_WINDOW: Record<Market, Window> = { TR: 30, US: 100 }

export function MovesPage() {
  const { market } = useMarket()
  const { t } = useI18n()
  const [params, setParams] = useSearchParams()
  const fund = params.get("fund")?.toUpperCase() || null  // the API upper-cases codes; keep the chip, its link and the match in step
  const [kind, setKind] = useState<MoveKind>("buys")
  // Remember the pick together with its market so switching markets falls back to that market's default without an effect.
  const [pick, setPick] = useState<{ market: Market; days: Window } | null>(null)
  const days = pick?.market === market ? pick.days : DEFAULT_WINDOW[market]
  const q = useQuery({ queryKey: ["moves", market, kind, days, fund], queryFn: () => api.moves(market, kind, days, fund), placeholderData: (prev) => prev })
  const clearFund = () => setParams((p) => { p.delete("fund"); return p })
  const d = q.data
  // While a different fund's rows are still on screen (placeholderData), show the code rather than the wrong name.
  const fundName = d?.fund?.code === fund ? d.fund.name : fund

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">{t("moves.title")}</h1>
          <p className="text-sm text-muted-foreground">{t("moves.sub")}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Segmented value={kind} options={KINDS.map((k) => ({ value: k, label: t(`moves.kind.${k}`) }))} onChange={setKind} />
          <Segmented value={days} options={WINDOWS[market].map((w) => ({ value: w, label: t(`moves.window.${w}`) }))} onChange={(w) => setPick({ market, days: w })} />
        </div>
      </div>

      {fund && (
        <div className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-xs">
          <span className="text-muted-foreground">{t("moves.fundFilter")}:</span>
          <Link to={`/funds/${fund}`} className="font-semibold hover:underline">{fundName}</Link>
          <button type="button" onClick={clearFund} aria-label={t("moves.clearFund")} title={t("moves.clearFund")} className="ml-0.5 rounded-sm px-1 text-muted-foreground hover:text-foreground">✕</button>
        </div>
      )}

      {q.isLoading ? (
        <Skeleton className="h-80" />
      ) : q.isError || !d ? (
        <MovesError fund={fund} notFound={q.error instanceof Error && /^404\b/.test(q.error.message)} onClear={clearFund} />
      ) : (
        <Section
          title={t(`moves.kind.${kind}`)}
          hint={`${d.window_days} ${t("common.days")} · ${fmtDate(d.window_start)} → ${fmtDate(d.as_of)} · ${t("moves.instruments", { n: d.total })}${d.total > d.rows.length ? ` · ${t("moves.showingFirst", { n: d.rows.length })}` : ""}`}
          className={cn(q.isPlaceholderData && "opacity-60 transition-opacity")}
        >
          <MovesTable rows={d.rows} market={market} />
        </Section>
      )}
    </div>
  )
}

function Segmented<V extends string | number>({ value, options, onChange }: { value: V; options: { value: V; label: string }[]; onChange: (v: V) => void }) {
  return (
    <div className="flex rounded-md border border-border p-0.5 text-xs">
      {options.map((o) => (
        <button key={String(o.value)} type="button" aria-pressed={o.value === value} onClick={() => onChange(o.value)} className={cn("rounded-[5px] px-3 py-1 font-medium transition", o.value === value ? "bg-secondary" : "text-muted-foreground hover:text-foreground")}>
          {o.label}
        </button>
      ))}
    </div>
  )
}

function MovesTable({ rows, market }: { rows: MoveRow[]; market: Market }) {
  const { t } = useI18n()
  if (rows.length === 0) return <div className="px-4 py-8 text-center text-sm text-muted-foreground">{t("moves.empty")}</div>
  return (
    <table className="w-full text-sm">
      <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
        <tr className="border-b border-border/60">
          <th className="px-4 py-2 text-left font-medium">{t("common.stock")}</th>
          <th className="px-2 py-2 text-right font-medium">{t("common.netFlow")}</th>
          <th className="hidden px-2 py-2 text-right font-medium sm:table-cell">{t("moves.netQty")}</th>
          <th className="px-4 py-2 text-left font-medium">{t("moves.parties")}</th>
        </tr>
      </thead>
      <tbody>
        {rows.map((r) => (
          <tr key={r.symbol} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
            <td className="px-4 py-2.5 align-top">
              <Link to={`/stocks/${r.symbol}`} className="font-semibold hover:underline">{r.symbol}</Link>
              <div className="max-w-[12rem] truncate text-xs text-muted-foreground">{r.name}</div>
            </td>
            <td className="px-2 py-2.5 text-right align-top"><Flow value={r.net_flow_value} market={market} /></td>
            <td className={cn("num hidden px-2 py-2.5 text-right align-top sm:table-cell", r.net_qty > 0 ? "text-positive" : r.net_qty < 0 ? "text-negative" : "text-muted-foreground")}>{fmtLots(r.net_qty)}</td>
            <td className="px-4 py-2.5 align-top">
              <div className="flex flex-wrap items-center gap-1">
                <span className="num mr-1 text-xs text-muted-foreground">{r.party_count}</span>
                {r.parties.map((p, i) => <PartyChip key={`${p.kind}:${p.code}:${i}`} party={p} market={market} />)}
                {r.party_count > r.parties.length && <span className="text-[11px] text-muted-foreground">{t("common.more", { n: r.party_count - r.parties.length })}</span>}
              </div>
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

/** "FUNDNAME · ADD +₺1.2M" in the activity's colour; a GROUPED event shows its institution instead of a fund. */
function PartyChip({ party: p, market }: { party: MoveParty; market: Market }) {
  return (
    <Link
      to={p.kind === "fund" ? `/funds/${p.code}` : `/institutions/${p.code}`}
      title={`${p.name} · ${fmtDate(p.period_end)}`}
      className={cn("inline-flex max-w-full items-center gap-1 rounded-sm border px-1.5 py-0.5 text-[11px] leading-4 hover:bg-accent/40", ACT[p.activity])}
    >
      <span className="max-w-[9rem] truncate text-foreground">{p.name}</span>
      <span className="opacity-60">·</span>
      <span className="font-semibold uppercase tracking-wider">{p.activity}</span>
      <span className="num">{p.delta_value === null ? fmtLots(p.delta_qty) : fmtMoney(p.delta_value, market)}</span>
      {p.confidence !== "INFERRED" && <ConfidenceBadge value={p.confidence} className="ml-0.5 px-1 py-0 text-[9px]" />}
    </Link>
  )
}

function MovesError({ fund, notFound, onClear }: { fund: string | null; notFound: boolean; onClear: () => void }) {
  const { t } = useI18n()
  return (
    <div className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground">
      {fund && notFound ? (
        <>
          <b className="text-foreground">{fund}</b> {t("fund.notFound")}
          <div className="mt-3"><button type="button" onClick={onClear} className="text-xs text-primary hover:underline">{t("moves.clearFund")}</button></div>
        </>
      ) : (
        t("moves.error")
      )}
    </div>
  )
}
