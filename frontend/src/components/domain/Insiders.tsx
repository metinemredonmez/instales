import { useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ExternalLink } from "lucide-react"
import { api, type InsiderCode, type InsiderStats, type InsiderTx, type InsiderWindow, type Insiders as InsidersData, type InsidersSummary, type Market } from "@/lib/api"
import { fmtCompact, fmtDate, fmtPrice, fmtQty } from "@/lib/format"
import { useI18n, type T } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { Segmented } from "@/components/ui/segmented"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const WINDOWS: InsiderWindow[] = [90, 180, 365]
/** Form 4 transaction codes with a label of their own; any other letter is printed as filed, with the "other" label. */
const CODES: readonly InsiderCode[] = ["P", "S", "A", "M", "F", "G", "D", "C", "X", "J", "W"]
const ROLES = ["director", "officer", "ten_percent_owner", "other"] as const
/** Rows shown before "show all" — a year of a large filer runs to the API's 200-row cap, which the payload flags as `truncated`. */
const FOLD = 30
/** Every Form 4 figure is in dollars: EDGAR filers report in USD. */
const CURRENCY = "USD"

const isKnown = (code: string): code is InsiderCode => (CODES as readonly string[]).includes(code)
const codeLabel = (t: T, code: string) => t(`ins.code.${isKnown(code) ? code : "J"}`)
/** "director,officer" → "Yönetim kurulu üyesi, Yönetici"; a token the contract does not name is shown as filed. */
const roleLabel = (t: T, role: string) =>
  role.split(",").map((r) => r.trim()).filter(Boolean).map((r) => ((ROLES as readonly string[]).includes(r) ? t(`ins.role.${r as (typeof ROLES)[number]}`) : r)).join(", ")
/** Money that changed hands is a flow: signed both ways (fmtCompact keeps a "-" but never adds a "+"), locale mantissa, dollar symbol. */
const signedCompact = (v: number) => `${v > 0 ? "+" : ""}${fmtCompact(v, CURRENCY)}`
const tone = (v: number) => (v > 0 ? "text-positive" : v < 0 ? "text-negative" : undefined)
/** Only open-market buys and sells carry a colour; a grant, an exercise or a tax withholding is neither and stays neutral. */
const openMarketTone = (code: string) => (code === "P" ? "text-positive" : code === "S" ? "text-negative" : undefined)
/** Whole days since the epoch of an ISO date or datetime, read as a calendar date (UTC) so the cluster's day count does not drift with the viewer's time zone. */
const calendarDay = (iso: string) => Math.floor(Date.parse(iso.slice(0, 10)) / 86_400_000)

/**
 * SEC Form 4 transactions of one US stock's insiders: the window summary (buyers, sellers, net value and the
 * open-market buy cluster when one is live), a 90/180/365-day toggle and the transaction table, every row linking to
 * its filing on EDGAR. Codes are labelled as the form defines them and figures are printed as filed. The section
 * hides itself when nothing has ever loaded and altogether on BIST symbols, where the API answers `supported: false`
 * (KAP insider filings come later); once a window has loaded it stays on screen, and a window that then fails to
 * load says so in one line rather than taking the section with it. An issuer the daily job has not read yet
 * (`fetched_at` null) says so instead of claiming there was no activity; a window the API cut at its 200-row cap says
 * so under the table, with the issuer's Form 4 list on EDGAR for the rest.
 */
export function Insiders({ symbol, market }: { symbol: string; market: Market }) {
  const { t } = useI18n()
  const [days, setDays] = useState<InsiderWindow>(90)
  const [all, setAll] = useState(false)
  const q = useQuery({ queryKey: ["insiders", market, symbol, days], queryFn: () => api.insiders(market, symbol, days), placeholderData: (prev) => prev })
  const last = useRef<InsidersData>(undefined)
  if (q.data) last.current = q.data
  const d = q.data ?? last.current

  if (!d) return q.isError ? null : <Skeleton className="h-64" />
  if (!d.supported) return null
  const rows = all ? d.transactions : d.transactions.slice(0, FOLD)

  return (
    <Section
      title={t("ins.title")}
      hint={t("ins.hint")}
      right={<Segmented value={days} options={WINDOWS.map((w) => ({ value: w, label: t(`ins.window.${w}`) }))} onChange={(w) => { setDays(w); setAll(false) }} />}
      className={cn(q.isPlaceholderData && "opacity-60 transition-opacity")}
    >
      {q.isError && <div className="px-4 py-2 text-xs text-negative">{t("ins.error")}</div>}
      {d.transactions.length === 0 ? (
        <div className="px-4 py-6 text-sm text-muted-foreground">{d.fetched_at === null ? t("ins.notFetched") : t("ins.empty", { n: d.days })}</div>
      ) : (
        <div className="divide-y divide-border/60">
          <SummaryStrip s={d.summary} days={d.days} asOf={d.as_of} />
          <TxTable rows={rows} />
          {rows.length < d.transactions.length && (
            <div className="px-4 py-2">
              <button type="button" onClick={() => setAll(true)} className="text-xs text-primary hover:underline">{t("ins.showAll", { n: d.transactions.length })}</button>
            </div>
          )}
          {d.truncated && (
            <div className="px-4 py-2 text-[11px] text-muted-foreground">
              {d.edgar_url ? (
                <a href={d.edgar_url} target="_blank" rel="noopener noreferrer" className="hover:underline">{t("ins.truncated", { n: d.transactions.length })}</a>
              ) : (
                t("ins.truncated", { n: d.transactions.length })
              )}
            </div>
          )}
          <Footnote t={t} />
        </div>
      )}
    </Section>
  )
}

/**
 * Header chip under the stock name: "İçeriden (90G) +3/−1" — insiders who bought and who sold over the summary
 * window; the border turns green while an open-market buy cluster is live (the signal badge names it). An issuer
 * read with no open-market buyer or seller in the window renders nothing: "+0/−0" in colour would be noise, and the
 * section below says it in words.
 */
export function InsidersChip({ s }: { s: InsidersSummary }) {
  const { t } = useI18n()
  if (s.buyers === 0 && s.sellers === 0 && !s.cluster) return null
  return (
    <span title={s.cluster ? t("signal.insiderBuyCluster") : undefined} className={cn("inline-flex items-center gap-1 rounded-sm border px-1.5 py-0.5", s.cluster ? "border-positive/40 bg-positive/10" : "border-border")}>
      <span className="text-muted-foreground">{t("ins.chip")} ({s.days}{t("common.dShort")})</span>
      <span className="num font-medium"><span className="text-positive">+{s.buyers}</span>/<span className="text-negative">−{s.sellers}</span></span>
    </span>
  )
}

/**
 * "Son 90 gün · 16 Eyl 2026" and the head-count chips. Buyer and seller counts take the activity card's tones, the
 * net value its own sign; the cluster chip repeats the signal's definition (distinct insiders, open market, the
 * days between its first purchase and `as_of`) so the badge in the header is explained where the rows are.
 */
function SummaryStrip({ s, days, asOf }: { s: InsiderStats; days: number; asOf: string }) {
  const { t } = useI18n()
  const clusterDays = s.cluster ? Math.max(1, calendarDay(asOf) - calendarDay(s.cluster.since)) : 0
  const chips: { label: string; value: string; cls?: string }[] = [
    { label: t("ins.buyers"), value: t("ins.people", { n: s.buyers }), cls: "text-positive" },
    { label: t("ins.sellers"), value: t("ins.people", { n: s.sellers }), cls: "text-negative" },
    { label: t("ins.netValue"), value: signedCompact(s.net_value), cls: tone(s.net_value) },
    { label: t("ins.openMarket"), value: t("ins.openMarketCount", { b: s.open_market_buys, s: s.open_market_sells }) },
  ]
  return (
    <div className="px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{t("ins.summary", { n: days })} · {fmtDate(asOf)}</div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
        {chips.map((c) => (
          <span key={c.label} className="inline-flex items-center gap-1.5 rounded-sm border border-border px-2 py-1">
            <span className="text-muted-foreground">{c.label}</span>
            <span className={cn("num font-medium", c.cls)}>{c.value}</span>
          </span>
        ))}
        {s.cluster && (
          <span className="inline-flex items-center gap-1.5 rounded-sm border border-positive/40 bg-positive/10 px-2 py-1 font-medium text-positive">
            ▲ {t("ins.cluster", { d: clusterDays, n: s.cluster.insiders })} · <span className="num">{fmtCompact(s.cluster.value, CURRENCY)}</span>
          </span>
        )}
      </div>
    </div>
  )
}

function TxTable({ rows }: { rows: InsiderTx[] }) {
  const { t } = useI18n()
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
          <tr className="border-b border-border/60">
            <th className="px-4 py-2 text-left font-medium">{t("ins.col.date")}</th>
            <th className="px-2 py-2 text-left font-medium">{t("ins.col.insider")}</th>
            <th className="px-2 py-2 text-left font-medium">{t("ins.col.type")}</th>
            <th className="px-2 py-2 text-right font-medium">{t("ins.col.shares")}</th>
            <th className="px-2 py-2 text-right font-medium">{t("ins.col.price")}</th>
            <th className="px-2 py-2 text-right font-medium">{t("ins.col.value")}</th>
            <th className="px-2 py-2 text-right font-medium">{t("ins.col.post")}</th>
            <th className="px-2 py-2 text-left font-medium">{t("ins.col.ownership")}</th>
            <th className="px-4 py-2" />
          </tr>
        </thead>
        <tbody>{rows.map((r) => <TxRow key={r.id} r={r} />)}</tbody>
      </table>
    </div>
  )
}

/**
 * Shares and value carry the filing's own (A)/(D) sign — an exercise is "+", a tax withholding "-" — while colour is
 * reserved for codes P and S. The code letter sits next to its label, as reported, so an uncommon code is never hidden
 * behind the "other" label.
 */
function TxRow({ r }: { r: InsiderTx }) {
  const { t } = useI18n()
  const cls = openMarketTone(r.code)
  const sub = [roleLabel(t, r.role), r.title].filter(Boolean).join(" · ")
  return (
    <tr className="border-b border-border/40 last:border-0 hover:bg-accent/40">
      <td className="num whitespace-nowrap px-4 py-2 align-top">
        {fmtDate(r.transaction_date)}
        <div className="text-[11px] text-muted-foreground">{t("ins.filed", { d: fmtDate(r.filed_at) })}</div>
      </td>
      <td className="px-2 py-2 align-top">
        <div className="font-medium">{r.insider}</div>
        {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
      </td>
      <td className={cn("whitespace-nowrap px-2 py-2 align-top", cls)}>
        <span className="mr-1.5 inline-flex rounded-sm border border-border px-1 font-mono text-[10px] text-muted-foreground">{r.code}</span>
        {codeLabel(t, r.code)}
        {r.derivative && <span className="ml-1.5 inline-flex rounded-sm border border-border px-1 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">{t("ins.derivative")}</span>}
      </td>
      <td className={cn("num whitespace-nowrap px-2 py-2 text-right align-top", cls)}>{r.acquired ? "+" : "-"}{fmtQty(r.shares)}</td>
      <td className="num whitespace-nowrap px-2 py-2 text-right align-top">{fmtPrice(r.price, CURRENCY)}</td>
      <td className={cn("num whitespace-nowrap px-2 py-2 text-right align-top", cls)}>{r.value === null ? "—" : signedCompact(r.acquired ? r.value : -r.value)}</td>
      <td className="num whitespace-nowrap px-2 py-2 text-right align-top text-muted-foreground">{r.post_shares === null ? "—" : fmtQty(r.post_shares)}</td>
      <td className="whitespace-nowrap px-2 py-2 align-top text-muted-foreground">{t(`ins.own.${r.ownership}`)}</td>
      <td className="px-4 py-2 text-right align-top">
        <a href={r.url} target="_blank" rel="noopener noreferrer" title={t("ins.edgar")} aria-label={t("ins.edgar")} className="inline-flex text-muted-foreground hover:text-foreground">
          <ExternalLink className="size-3.5" />
        </a>
      </td>
    </tr>
  )
}

/** "Kaynak: SEC EDGAR Form 4 · işlem kodları raporlandığı gibi · tavsiye değildir" — provenance travels with the rows. */
function Footnote({ t }: { t: T }) {
  return <div className="px-4 py-2 text-[11px] text-muted-foreground">{t("quotes.source", { src: t("ins.source") })} · {t("ins.disclaimer")}</div>
}
