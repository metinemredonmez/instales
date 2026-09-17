import { useRef, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { ExternalLink } from "lucide-react"
import { api, type InsiderCode, type InsiderPartyKind, type InsiderSource, type InsiderStats, type InsiderTx, type InsiderWindow, type Insiders as InsidersData, type InsidersSummary, type Market } from "@/lib/api"
import { fmtCompact, fmtDate, fmtNum, fmtPrice, fmtQty, locale } from "@/lib/format"
import { useI18n, type T } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { Segmented } from "@/components/ui/segmented"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const WINDOWS: InsiderWindow[] = [90, 180, 365]
/** Form 4 transaction codes with a label of their own; any other letter is printed as filed, with the "other" label. */
const CODES: readonly InsiderCode[] = ["P", "S", "A", "M", "F", "G", "D", "C", "X", "J", "W"]
const ROLES = ["director", "officer", "ten_percent_owner", "shareholder", "issuer", "other"] as const
/** Rows shown before "show all" — a year of a large filer runs to the API's 200-row cap, which the payload flags as `truncated`. */
const FOLD = 30
/** Every Form 4 figure is in dollars (EDGAR filers report in USD); a KAP filing's price and value are in lira. */
const currencyOf = (source: InsiderSource) => (source === "kap" ? "TRY" : "USD")

const isKnown = (code: string): code is InsiderCode => (CODES as readonly string[]).includes(code)
/** KAP has no code table: its two rows are worded as the filing does ("Alış"/"Satış"), Form 4 codes as the form defines them. */
const codeLabel = (t: T, code: string, kap: boolean) => (kap && (code === "P" || code === "S") ? t(`ins.code.kap.${code}`) : t(`ins.code.${isKnown(code) ? code : "J"}`))
/** "director,officer" → "Yönetim kurulu üyesi, Yönetici"; a token the contract does not name is shown as filed. */
const roleLabel = (t: T, role: string) =>
  role.split(",").map((r) => r.trim()).filter(Boolean).map((r) => ((ROLES as readonly string[]).includes(r) ? t(`ins.role.${r as (typeof ROLES)[number]}`) : r)).join(", ")
/** Money that changed hands is a flow: signed both ways (fmtCompact keeps a "-" but never adds a "+"), locale mantissa, currency symbol. */
const signedCompact = (v: number, currency: string) => `${v > 0 ? "+" : ""}${fmtCompact(v, currency)}`
const tone = (v: number) => (v > 0 ? "text-positive" : v < 0 ? "text-negative" : undefined)
/**
 * The issuer trading its own shares (the API's `buyback`, role "issuer"): listed as filed, never an insider purchase —
 * so neither summed nor coloured. `party_kind` alone does not say: a controlling holding company buying the stock is
 * a `company` too, and that is a shareholder's purchase.
 */
const isBuyback = (r: InsiderTx) => r.buyback
/** Only open-market buys and sells carry a colour; a grant, an exercise, a tax withholding or a buyback is neither and stays neutral. */
const openMarketTone = (r: InsiderTx) => (isBuyback(r) ? undefined : r.code === "P" ? "text-positive" : r.code === "S" ? "text-negative" : undefined)
/**
 * The chip next to the code on a KAP row whose acting party is not a person: the company's own-share trade first — a
 * buyback when it acquired, a treasury-share sale when it disposed (the API's `buyback` flag covers both sides, so
 * `acquired` tells them apart; the filing never says "geri alım" of a sale) — else the party's kind (a holding, a fund).
 */
const partyChip = (r: InsiderTx): "buyback" | "treasurySale" | Exclude<InsiderPartyKind, "person"> | null =>
  isBuyback(r) ? (r.acquired ? "buyback" : "treasurySale") : r.party_kind && r.party_kind !== "person" ? r.party_kind : null
/** "₺103,90–104,00": a KAP filing that states only a price range, printed as filed — the value column stays empty rather than guessing a midpoint. */
const priceCell = (r: InsiderTx, currency: string) => (r.price === null && r.price_range ? `${fmtPrice(r.price_range[0], currency)}–${fmtNum(r.price_range[1], 2)}` : fmtPrice(r.price, currency))
/** A stake as the filing states it — "%0,0758", "%55,14", "%2,0379": up to the four decimals the form carries, no trailing zeros, never rounded to two. */
const fmtStake = (v: number) => new Intl.NumberFormat(locale(), { minimumFractionDigits: 0, maximumFractionDigits: 4 }).format(v)
/** The window's start, an ISO date, for the coverage check: `as_of` minus `days`. */
const windowStart = (asOf: string, days: number) => new Date(Date.parse(asOf.slice(0, 10)) - days * 86_400_000).toISOString().slice(0, 10)
/** Whole days since the epoch of an ISO date or datetime, read as a calendar date (UTC) so the cluster's day count does not drift with the viewer's time zone. */
const calendarDay = (iso: string) => Math.floor(Date.parse(iso.slice(0, 10)) / 86_400_000)

/**
 * One stock's insider transactions: the window summary (buyers, sellers, net value and the buy cluster when one is
 * live), a 90/180/365-day toggle and the transaction table, every row linking to its filing. On US issuers the rows
 * are SEC Form 4 transactions (codes labelled as the form defines them, dollars, EDGAR links); on BIST they are the
 * KAP "Pay Alım Satım Bildirimi" filings published under the issuer (`source: "kap"` — buys and sells worded as the
 * filing does, lira, a stated price range as a range, KAP links), where a filing by the company itself is its own-share
 * trade (a buyback or a treasury-share sale): kept in the list under its own chip, left out of the summary, and the
 * strip says so. Figures are printed as filed. The section hides itself when nothing has ever loaded and altogether
 * where the API answers `supported: false`; once a window has loaded it stays on screen, and a window that then fails
 * to load says so in one line rather than taking the section with it. A source the job has not read yet (`fetched_at`
 * null) says so instead of claiming there was no activity, and an empty BIST window that starts before the KAP feed's
 * `coverage_since` says "nothing since that day" rather than "nothing in 90 days"; a window the API cut at its 200-row
 * cap says so under the table, linking the issuer's list at the source (`more_url`) for the rest when there is one.
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
  const kap = d.source === "kap"
  const currency = currencyOf(d.source)
  const rows = all ? d.transactions : d.transactions.slice(0, FOLD)

  return (
    <Section
      title={t("ins.title")}
      hint={t(kap ? "ins.hint.kap" : "ins.hint")}
      right={<Segmented value={days} options={WINDOWS.map((w) => ({ value: w, label: t(`ins.window.${w}`) }))} onChange={(w) => { setDays(w); setAll(false) }} />}
      className={cn(q.isPlaceholderData && "opacity-60 transition-opacity")}
    >
      {q.isError && <div className="px-4 py-2 text-xs text-negative">{t("ins.error")}</div>}
      {d.transactions.length === 0 ? (
        <div className="px-4 py-6 text-sm text-muted-foreground">
          {d.fetched_at === null
            ? t(kap ? "ins.notFetched.kap" : "ins.notFetched")
            : d.coverage_since && d.coverage_since > windowStart(d.as_of, d.days)
              ? t("ins.emptySince", { d: fmtDate(d.coverage_since) })
              : t("ins.empty", { n: d.days })}
        </div>
      ) : (
        <div className="divide-y divide-border/60">
          <SummaryStrip s={d.summary} days={d.days} asOf={d.as_of} kap={kap} currency={currency} buybacks={d.transactions.some(isBuyback)} />
          <TxTable rows={rows} kap={kap} currency={currency} />
          {rows.length < d.transactions.length && (
            <div className="px-4 py-2">
              <button type="button" onClick={() => setAll(true)} className="text-xs text-primary hover:underline">{t("ins.showAll", { n: d.transactions.length })}</button>
            </div>
          )}
          {d.truncated && (
            <div className="px-4 py-2 text-[11px] text-muted-foreground">
              {d.more_url ? (
                <a href={d.more_url} target="_blank" rel="noopener noreferrer" className="hover:underline">{t(kap ? "ins.truncated.kap" : "ins.truncated", { n: d.transactions.length })}</a>
              ) : (
                t(kap ? "ins.truncated.kap" : "ins.truncated", { n: d.transactions.length })
              )}
            </div>
          )}
          <Footnote t={t} kap={kap} />
        </div>
      )}
    </Section>
  )
}

/**
 * Header chip under the stock name: "İçeriden (90G) +3/−1" — insiders who bought and who sold over the summary
 * window; the border turns green while a buy cluster is live (the signal badge names it). An issuer read with no
 * buyer or seller in the window renders nothing: "+0/−0" in colour would be noise, and the section below says it in
 * words.
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
 * days between its first purchase and `as_of`) so the badge in the header is explained where the rows are. A KAP
 * list that holds the company's own-share trade says in the head line that the summary leaves those out, next to the
 * numbers it would otherwise seem to contradict.
 */
function SummaryStrip({ s, days, asOf, kap, currency, buybacks }: { s: InsiderStats; days: number; asOf: string; kap: boolean; currency: string; buybacks: boolean }) {
  const { t } = useI18n()
  const clusterDays = s.cluster ? Math.max(1, calendarDay(asOf) - calendarDay(s.cluster.since)) : 0
  const chips: { label: string; value: string; cls?: string }[] = [
    { label: t("ins.buyers"), value: t("ins.people", { n: s.buyers }), cls: "text-positive" },
    { label: t("ins.sellers"), value: t("ins.people", { n: s.sellers }), cls: "text-negative" },
    { label: t("ins.netValue"), value: signedCompact(s.net_value, currency), cls: tone(s.net_value) },
    { label: t(kap ? "ins.trades" : "ins.openMarket"), value: t(kap ? "ins.tradesCount" : "ins.openMarketCount", { b: s.open_market_buys, s: s.open_market_sells }) },
  ]
  return (
    <div className="px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{t("ins.summary", { n: days })} · {fmtDate(asOf)}{buybacks && ` · ${t("ins.buybackNote")}`}</div>
      <div className="mt-2 flex flex-wrap gap-1.5 text-xs">
        {chips.map((c) => (
          <span key={c.label} className="inline-flex items-center gap-1.5 rounded-sm border border-border px-2 py-1">
            <span className="text-muted-foreground">{c.label}</span>
            <span className={cn("num font-medium", c.cls)}>{c.value}</span>
          </span>
        ))}
        {s.cluster && (
          <span className="inline-flex items-center gap-1.5 rounded-sm border border-positive/40 bg-positive/10 px-2 py-1 font-medium text-positive">
            ▲ {t(kap ? "ins.cluster.kap" : "ins.cluster", { d: clusterDays, n: s.cluster.insiders })} · <span className="num">{fmtCompact(s.cluster.value, currency)}</span>
          </span>
        )}
      </div>
    </div>
  )
}

function TxTable({ rows, kap, currency }: { rows: InsiderTx[]; kap: boolean; currency: string }) {
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
        <tbody>{rows.map((r) => <TxRow key={r.id} r={r} kap={kap} currency={currency} />)}</tbody>
      </table>
    </div>
  )
}

/**
 * Shares and value carry the filing's own (A)/(D) sign — an exercise is "+", a tax withholding "-" — while colour is
 * reserved for codes P and S of a person's own trade. The code letter sits next to its label, as reported, so an
 * uncommon code is never hidden behind the "other" label. A KAP row whose acting party is not a person wears a
 * neutral chip naming what it is (the issuer's buyback or treasury-share sale, a holding company, a fund). The
 * post-transaction cell prints the share count when the filing states one (Form 4) and the stake alone when it
 * states only that (KAP: `post_shares` is never filled, and the stake keeps the filing's four decimals); the
 * ownership cell is "—" on KAP, where a filing states no direct / indirect nature.
 */
function TxRow({ r, kap, currency }: { r: InsiderTx; kap: boolean; currency: string }) {
  const { t } = useI18n()
  const cls = openMarketTone(r)
  const sub = [roleLabel(t, r.role), r.title].filter(Boolean).join(" · ")
  const party = partyChip(r)
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
        {codeLabel(t, r.code, kap)}
        {r.derivative && <span className="ml-1.5 inline-flex rounded-sm border border-border px-1 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">{t("ins.derivative")}</span>}
        {party && <span className="ml-1.5 inline-flex rounded-sm border border-border px-1 py-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">{t(`ins.party.${party}`)}</span>}
      </td>
      <td className={cn("num whitespace-nowrap px-2 py-2 text-right align-top", cls)}>{r.acquired ? "+" : "-"}{fmtQty(r.shares)}</td>
      <td className="num whitespace-nowrap px-2 py-2 text-right align-top">{priceCell(r, currency)}</td>
      <td className={cn("num whitespace-nowrap px-2 py-2 text-right align-top", cls)}>{r.value === null ? "—" : signedCompact(r.acquired ? r.value : -r.value, currency)}</td>
      <td className="num whitespace-nowrap px-2 py-2 text-right align-top text-muted-foreground">
        {r.post_shares === null ? (r.post_pct_stake == null ? "—" : t("ins.pct", { p: fmtStake(r.post_pct_stake) })) : fmtQty(r.post_shares)}
        {r.post_shares !== null && r.post_pct_stake != null && <div className="text-[11px]">{t("ins.pct", { p: fmtStake(r.post_pct_stake) })}</div>}
      </td>
      <td className="whitespace-nowrap px-2 py-2 align-top text-muted-foreground">{kap ? "—" : t(`ins.own.${r.ownership}`)}</td>
      <td className="px-4 py-2 text-right align-top">
        <a href={r.url} target="_blank" rel="noopener noreferrer" title={t(kap ? "ins.kap" : "ins.edgar")} aria-label={t(kap ? "ins.kap" : "ins.edgar")} className="inline-flex text-muted-foreground hover:text-foreground">
          <ExternalLink className="size-3.5" />
        </a>
      </td>
    </tr>
  )
}

/** "Kaynak: SEC EDGAR Form 4 · işlem kodları raporlandığı gibi · tavsiye değildir" (KAP: its own wording) — provenance travels with the rows. */
function Footnote({ t, kap }: { t: T; kap: boolean }) {
  return <div className="px-4 py-2 text-[11px] text-muted-foreground">{t("quotes.source", { src: t(kap ? "ins.source.kap" : "ins.source") })} · {t(kap ? "ins.disclaimer.kap" : "ins.disclaimer")}</div>
}
