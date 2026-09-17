import { useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { ChevronDown } from "lucide-react"
import { api, type Crowding, type CrowdingLevel, type CrowdingScore, type Holder, type Market, type Ownership as OwnershipData } from "@/lib/api"
import { fmtCompact, fmtDate, fmtNum, fmtQty } from "@/lib/format"
import { useI18n, type T } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { Skeleton } from "@/components/ui/skeleton"
import { ActivityBadge, ConfidenceBadge, CrowdingPill, crowdingLevel, scoreLabel } from "./badges"
import { cn } from "@/lib/utils"

/** Crowding components as engine/crowding.py names them; a key outside this map (a future component) is shown as written, underscores as spaces. */
const COMPONENT_KEY: Record<string, "own.c.holders" | "own.c.held_pct" | "own.c.concentration" | "own.c.momentum"> = {
  holders: "own.c.holders",
  held_pct: "own.c.held_pct",
  concentration: "own.c.concentration",
  momentum: "own.c.momentum",
}
const componentLabel = (t: T, key: string) => (key in COMPONENT_KEY ? t(COMPONENT_KEY[key]) : key.replaceAll("_", " "))
const LEVELS: readonly CrowdingLevel[] = ["low", "medium", "high"]
const isLevel = (v: unknown): v is CrowdingLevel => typeof v === "string" && (LEVELS as readonly string[]).includes(v)
const asNum = (v: unknown): number | null => (typeof v === "number" && Number.isFinite(v) ? v : null)

/** The pct figures the API sends are ×100 already; two decimals for a share of shares outstanding (0.42 % is a real holding), one elsewhere. */
const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : `${fmtNum(v, digits)}%`)

interface WhyRow { key: string; raw: number | null; contribution: number; weight: number | null; skipped: boolean }
/**
 * The "why" JSON, one row per component — the objects carrying a `contribution`, in the engine's order. A plain
 * number in the object (`stale_holders`), a level string or a weights table is context, not a component, and is skipped.
 */
export function whyRows(why: Record<string, unknown>): WhyRow[] {
  return Object.entries(why).flatMap(([key, v]) => {
    if (!v || typeof v !== "object") return []
    const o = v as Record<string, unknown>
    const contribution = asNum(o.contribution)
    return contribution === null ? [] : [{ key, raw: asNum(o.raw ?? o.value), contribution, weight: asNum(o.weight), skipped: !!o.skipped }]
  })
}
/** The stored score row's level: the row's own field, else one written into `why`, else the documented thresholds. */
export const levelOf = (s: CrowdingScore): CrowdingLevel => s.level ?? (isLevel(s.why.level) ? s.why.level : crowdingLevel(s.score))

/**
 * Who holds one stock: every fund's latest portfolio report (one row per fund), the totals strip, the crowding score
 * with its components on demand, and the holders table with each fund's last move and lineage. Everything on screen is
 * a figure the API sent or a dash; the share of shares outstanding says "share count unknown" when there is no count to
 * divide by rather than printing a zero. The section hides itself when nothing has ever loaded; once loaded it stays on
 * screen, and a refetch that then fails says so in one line — for the same stock only: the stock page is not remounted
 * when its symbol changes, so the remembered payload is keyed by market and symbol and a placeholder (the previous
 * stock's data while the next one loads) is never remembered, or a failed load would show the previous stock's
 * holders under the new name. Funds whose report is older than two reporting periods are not rows — the API counts
 * them and the note under the table says how many were left out.
 */
export function Ownership({ symbol, market }: { symbol: string; market: Market }) {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["ownership", market, symbol], queryFn: () => api.ownership(market, symbol), placeholderData: (prev) => prev })
  const key = `${market}:${symbol}`
  const last = useRef<{ key: string; data: OwnershipData }>(undefined)
  if (q.data && !q.isPlaceholderData) last.current = { key, data: q.data }
  const d = q.data ?? (last.current?.key === key ? last.current.data : undefined)

  if (!d) return q.isError ? null : <Skeleton className="h-64" />
  const notes = [
    d.totals.holders > d.holders.length ? t("own.showing", { n: d.holders.length }) : null,
    d.stale_holders > 0 ? t("own.stale", { n: d.stale_holders }) : null,
  ].filter(Boolean)

  return (
    <Section
      title={t("own.title")}
      hint={t("own.hint")}
      right={d.crowding ? <CrowdingPill score={d.crowding.score} level={d.crowding.level} /> : undefined}
      className={cn(q.isPlaceholderData && "opacity-60 transition-opacity")}
    >
      {q.isError && <div className="px-4 py-2 text-xs text-negative">{t("own.error")}</div>}
      {d.holders.length === 0 ? (
        <div className="px-4 py-6 text-sm text-muted-foreground">
          {t("own.empty")}
          {d.stale_holders > 0 && <div className="mt-1 text-xs">{t("own.stale", { n: d.stale_holders })}</div>}
        </div>
      ) : (
        <div className="divide-y divide-border/60">
          <TotalsStrip d={d} />
          {d.crowding && <CrowdingCard c={d.crowding} />}
          <HoldersTable rows={d.holders} currency={d.currency} />
          {notes.length > 0 && <div className="px-4 py-2 text-xs text-muted-foreground">{notes.join(" · ")}</div>}
          <Footnote t={t} />
        </div>
      )}
    </Section>
  )
}

/**
 * Header chip under the stock name, from stock_detail.scores.CROWDING: "Kalabalıklaşma 72 · yüksek kalabalıklaşma".
 * Same pill as the section's own, so the two never disagree on the level; the tooltip carries the score's definition.
 */
export function CrowdingChip({ s }: { s: CrowdingScore }) {
  const { t } = useI18n()
  return <CrowdingPill label={t("own.crowding")} score={s.score} level={levelOf(s)} size="sm" title={t("scores.crowding.desc")} />
}

/**
 * "en son rapor · 15 Eyl 2026" and the totals: funds · institutions, shares held, their market value (the tooltip
 * says how many reports state no value and so are not in it), the share of shares outstanding (or that the count is
 * unknown, with the count's own date in the tooltip when there is one), the top-10 share of the held quantity and the
 * HHI — each with its definition as a tooltip.
 */
function TotalsStrip({ d }: { d: OwnershipData }) {
  const { t } = useI18n()
  const tot = d.totals
  const chips: { label: string; value: string; title?: string; muted?: boolean }[] = [
    { label: t("own.holders"), value: t("own.fundsInstitutions", { f: tot.holders, i: tot.institutions }) },
    { label: t("own.quantity"), value: fmtQty(tot.quantity) },
    { label: t("own.marketValue"), value: fmtCompact(tot.market_value, d.currency), title: tot.unvalued_holders ? t("own.unvalued", { n: tot.unvalued_holders }) : undefined },
    tot.pct_of_shares === null
      ? { label: t("own.pctOfShares"), value: t("own.sharesUnknown"), muted: true }
      : { label: t("own.pctOfShares"), value: pct(tot.pct_of_shares, 2), title: d.shares_as_of ? t("own.sharesAsOf", { d: fmtDate(d.shares_as_of) }) : undefined },
    { label: t("own.top10"), value: pct(tot.top10_pct_of_held), title: t("own.top10.hint") },
    { label: t("own.hhi"), value: tot.hhi === null ? "—" : fmtNum(tot.hhi, 0), title: t("own.hhi.hint") },
  ]
  return (
    <div className="px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{t("own.latest")} · {fmtDate(d.as_of)}</div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
        {chips.map((c) => (
          <span key={c.label} title={c.title} className="inline-flex items-center gap-1.5 rounded-sm border border-border px-2 py-1">
            <span className="text-muted-foreground">{c.label}</span>
            <span className={cn("font-medium", c.muted ? "text-muted-foreground" : "num")}>{c.value}</span>
          </span>
        ))}
      </div>
    </div>
  )
}

/**
 * The crowding score with a "why" toggle: one row per component with its raw input, the points it added and the
 * weight applied, the bar being those points out of the 100 the score runs to — so the rows add up to the number in
 * the pill. A component the engine skipped (held_pct without a share count) says so in place of its raw value; its
 * weight reads 0 and the others are the rescaled ones the engine actually used.
 */
function CrowdingCard({ c }: { c: Crowding }) {
  const { t } = useI18n()
  const [open, setOpen] = useState(false)
  const rows = whyRows(c.why)
  return (
    <div className="px-4 py-3">
      <div className="flex flex-wrap items-center gap-3">
        <span className="text-[11px] uppercase tracking-wider text-muted-foreground">{scoreLabel(t, "CROWDING")}</span>
        <CrowdingPill score={c.score} level={c.level} />
        {rows.length > 0 && (
          <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} className="inline-flex items-center gap-1 text-xs text-primary hover:underline">
            {t("score.why", { n: Math.round(c.score) })} <ChevronDown className={cn("size-3 transition", open && "rotate-180")} />
          </button>
        )}
      </div>
      {open && (
        <div className="mt-3 space-y-1.5 border-t border-border/60 pt-3">
          <div className="text-[11px] text-muted-foreground">{t("own.why.hint")}</div>
          <ul>
            {rows.map((r) => (
              <li key={r.key} className="py-0.5 text-xs">
                <div className="flex justify-between gap-3">
                  <span className={cn(r.skipped && "text-muted-foreground")}>{componentLabel(t, r.key)}</span>
                  <span className="num whitespace-nowrap text-muted-foreground">
                    {r.skipped ? t("own.sharesUnknown") : r.raw === null ? "—" : fmtNum(r.raw, Number.isInteger(r.raw) ? 0 : 1)}
                    {" · "}{fmtNum(r.contribution, 1, true)}
                    {r.weight !== null && <> · {t("score.weight")} {Math.round(r.weight * 100)}</>}
                  </span>
                </div>
                <div className="mt-0.5 h-1 overflow-hidden rounded-full bg-muted"><div className="h-full bg-primary" style={{ width: `${Math.max(0, Math.min(100, r.contribution))}%` }} /></div>
              </li>
            ))}
          </ul>
          <div className="text-[11px] text-muted-foreground">{t("scores.crowding.desc")}</div>
        </div>
      )}
    </div>
  )
}

function HoldersTable({ rows, currency }: { rows: Holder[]; currency: string }) {
  const { t } = useI18n()
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
          <tr className="border-b border-border/60">
            <th className="px-4 py-2 text-left font-medium">{t("common.fund")}</th>
            <th className="px-2 py-2 text-left font-medium">{t("inst.institution")}</th>
            <th className="px-2 py-2 text-right font-medium">{t("common.lots")}</th>
            <th className="px-2 py-2 text-right font-medium">{t("own.marketValue")}</th>
            <th className="px-2 py-2 text-right font-medium">{t("own.col.weight")}</th>
            <th className="px-2 py-2 text-right font-medium">{t("own.col.pctOfShares")}</th>
            <th className="px-2 py-2 text-left font-medium">{t("own.col.lastMove")}</th>
            <th className="px-2 py-2 text-left font-medium">{t("common.confidence")}</th>
            <th className="px-4 py-2 text-right font-medium">{t("own.col.asOf")}</th>
          </tr>
        </thead>
        <tbody>{rows.map((r) => <HolderRow key={r.fund} r={r} currency={currency} />)}</tbody>
      </table>
    </div>
  )
}

/** One fund at its latest report. The last move is the fund's activity in its most recent period, dated, and a fund with no position change yet shows a dash. */
function HolderRow({ r, currency }: { r: Holder; currency: string }) {
  return (
    <tr className="border-b border-border/40 last:border-0 hover:bg-accent/40">
      <td className="px-4 py-2 align-top">
        <Link to={`/funds/${r.fund}`} className="font-mono font-semibold hover:underline">{r.fund}</Link>
        {r.name && r.name !== r.fund && <div className="max-w-56 truncate text-[11px] text-muted-foreground" title={r.name}>{r.name}</div>}
      </td>
      <td className="px-2 py-2 align-top text-muted-foreground">{r.institution}</td>
      <td className="num whitespace-nowrap px-2 py-2 text-right align-top">{fmtQty(r.quantity)}</td>
      <td className="num whitespace-nowrap px-2 py-2 text-right align-top">{fmtCompact(r.market_value, currency)}</td>
      <td className="num whitespace-nowrap px-2 py-2 text-right align-top">{pct(r.weight_pct)}</td>
      <td className="num whitespace-nowrap px-2 py-2 text-right align-top text-muted-foreground">{pct(r.pct_of_shares, 2)}</td>
      <td className="whitespace-nowrap px-2 py-2 align-top">
        {r.last_move ? (
          <span className="inline-flex items-center gap-1.5">
            <ActivityBadge value={r.last_move} />
            {r.last_move_period_end && <span className="num text-[11px] text-muted-foreground">{fmtDate(r.last_move_period_end)}</span>}
          </span>
        ) : (
          <span className="text-muted-foreground">—</span>
        )}
      </td>
      <td className="px-2 py-2 align-top"><ConfidenceBadge value={r.confidence} /></td>
      <td className="num whitespace-nowrap px-4 py-2 text-right align-top text-muted-foreground">{fmtDate(r.as_of)}</td>
    </tr>
  )
}

/** "Kaynak: fon portföy raporları (KAP) / 13F · en son rapor · tavsiye değildir" — provenance travels with the rows. */
function Footnote({ t }: { t: T }) {
  return <div className="px-4 py-2 text-[11px] text-muted-foreground">{t("quotes.source", { src: t("own.source") })} · {t("own.latest")} · {t("own.disclaimer")}</div>
}
