import { useQuery } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { useEffect, useId, useMemo, useState, type FormEvent, type KeyboardEvent } from "react"
import { api, type FundCompare, type FundOverlap, type Market, type OverlapPair } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { fmtDate, fmtNum } from "@/lib/format"
import { useI18n } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { ActivityBadge } from "@/components/domain/badges"
import { Button } from "@/components/ui/button"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

/** /funds/overlap accepts 2..6 codes; the picker closes once the set is full. */
export const MAX_FUNDS = 6

/** Weights arrive ×100; a fund without weights (or a pair where either side lacks them) is a dash, never a zero. */
const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? "—" : `${fmtNum(v, digits)}%`)

/**
 * The fund set from the URL: `?codes=TMV,MAC,ABC` is canonical; `?a=&b=` is the two-fund form the fund page and older
 * links still use. Upper-cased, de-duplicated, cut at the API's limit — a link with seven codes compares the first six.
 */
export function codesFromParams(params: URLSearchParams): string[] {
  const raw = params.get("codes") ? params.get("codes")!.split(",") : [params.get("a") ?? "", params.get("b") ?? ""]
  const out: string[] = []
  for (const c of raw.map((x) => x.trim().toUpperCase())) if (c && !out.includes(c)) out.push(c)
  return out.slice(0, MAX_FUNDS)
}

/**
 * Compare 2..6 funds of the active market: with exactly two, the pair detail (common positions with both funds'
 * moves, the "only" lists, both-increasing / both-reducing, opposite views) that /funds/{a}/compare/{b} answers; with
 * three or more, the pairwise overlap matrix and the stocks every fund holds from /funds/overlap. Both views are
 * fed from each fund's latest portfolio report. The set lives in the URL so a comparison is a link.
 */
export function ComparePage() {
  const { t } = useI18n()
  const { market } = useMarket()
  const [params, setParams] = useSearchParams()
  const codes = useMemo(() => codesFromParams(params), [params])
  const setCodes = (next: string[]) => setParams(next.length ? { codes: next.join(",") } : {})
  const add = (code: string) => { if (!codes.includes(code) && codes.length < MAX_FUNDS) setCodes([...codes, code]) }
  const remove = (code: string) => setCodes(codes.filter((c) => c !== code))

  const pair = useQuery({ queryKey: ["compare", codes[0], codes[1]], queryFn: () => api.compare(codes[0], codes[1]), enabled: codes.length === 2 })
  const overlap = useQuery({ queryKey: ["overlap", codes], queryFn: () => api.fundOverlap(codes), enabled: codes.length >= 3 })
  const active = codes.length === 2 ? pair : codes.length >= 3 ? overlap : null

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div><h1 className="text-2xl font-semibold tracking-tight">{t("compare.title")}</h1><p className="text-sm text-muted-foreground">{t("compare.sub")}</p></div>
        <FundPicker market={market} exclude={codes} disabled={codes.length >= MAX_FUNDS} onPick={add} />
      </div>

      <div className="flex flex-wrap items-center gap-1.5" aria-label={t("compare.funds")}>
        {codes.map((c) => (
          <span key={c} className="inline-flex items-center gap-1.5 rounded-md border border-primary/40 bg-primary/10 px-2 py-1 text-xs">
            <Link to={`/funds/${c}`} className="font-mono font-semibold hover:underline">{c}</Link>
            <button type="button" onClick={() => remove(c)} aria-label={t("compare.remove", { f: c })} title={t("compare.remove", { f: c })} className="ml-0.5 rounded-sm px-1 text-muted-foreground hover:text-foreground">✕</button>
          </span>
        ))}
        <span className="text-xs text-muted-foreground">{codes.length < 2 ? t("compare.min") : t("compare.max", { n: MAX_FUNDS })}</span>
      </div>

      {active?.isLoading && <Skeleton className="h-64" />}
      {active?.isError && <div className="rounded-md border border-negative/40 bg-negative/10 px-3 py-2 text-sm">{errorText(active.error, t)}</div>}
      {codes.length === 2 && pair.data && <PairDetail d={pair.data} />}
      {codes.length >= 3 && overlap.data && <OverlapView d={overlap.data} />}
    </div>
  )
}

/** 404 → one of the codes is unknown to the API (it does not say which); 422 → mixed markets or too few codes; anything else is a plain load error. */
function errorText(e: unknown, t: ReturnType<typeof useI18n>["t"]): string {
  const msg = e instanceof Error ? e.message : ""
  if (/^404\b/.test(msg)) return t("compare.notFound")
  if (/^422\b/.test(msg)) return t("compare.mixed")
  return t("compare.error")
}

/**
 * Typeahead for one more fund: /search of the active market, fund hits only, minus the funds already in the set.
 * Enter takes the highlighted hit, or the typed code as-is when nothing matched (the API then answers 404 and the page
 * says so) — the same shape-based fallback as the header search, without the navigation.
 */
function FundPicker({ market, exclude, disabled, onPick }: { market: Market; exclude: string[]; disabled: boolean; onPick: (code: string) => void }) {
  const { t } = useI18n()
  const [q, setQ] = useState("")
  const [debounced, setDebounced] = useState("")
  const [open, setOpen] = useState(false)
  const [activeIdx, setActiveIdx] = useState(0)
  const listId = useId()
  useEffect(() => {
    const h = setTimeout(() => setDebounced(q.trim()), 150)
    return () => clearTimeout(h)
  }, [q])
  const hits = useQuery({ queryKey: ["search", market, debounced], queryFn: () => api.search(market, debounced), enabled: debounced.length > 0, staleTime: 60_000, placeholderData: (prev) => prev })
  const rows = (debounced ? hits.data ?? [] : []).filter((h) => h.kind === "fund" && !exclude.includes(h.key.toUpperCase()))
  useEffect(() => { setActiveIdx(0) }, [rows.length, debounced])

  const pick = (code: string) => {
    const c = code.trim().toUpperCase()
    if (c) onPick(c)
    setQ(""); setDebounced(""); setOpen(false)
  }
  const submit = (e: FormEvent) => { e.preventDefault(); pick(rows[activeIdx]?.key ?? q) }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActiveIdx((a) => Math.min(a + 1, rows.length - 1)) }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActiveIdx((a) => Math.max(a - 1, 0)) }
    else if (e.key === "Escape") setOpen(false)
  }
  const showList = open && rows.length > 0

  return (
    <form onSubmit={submit} className="relative flex items-center gap-2">
      <input
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onKeyDown={onKey}
        disabled={disabled}
        role="combobox"
        aria-label={t("compare.add")}
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        placeholder={disabled ? t("compare.max", { n: MAX_FUNDS }) : t("compare.addPh")}
        className="h-9 w-48 rounded-md border border-input bg-card px-3 font-mono text-sm outline-none placeholder:font-sans placeholder:text-muted-foreground focus:ring-2 focus:ring-ring/40 disabled:opacity-60"
      />
      <Button type="submit" size="sm" disabled={disabled || !q.trim()}>{t("compare.add")}</Button>
      {showList && (
        <ul id={listId} role="listbox" className="absolute left-0 right-0 top-full z-40 mt-1 max-h-80 overflow-auto rounded-md border border-border bg-popover p-1 text-sm shadow-lg">
          {rows.map((h, i) => (
            <li
              key={h.key}
              role="option"
              aria-selected={i === activeIdx}
              onMouseEnter={() => setActiveIdx(i)}
              onMouseDown={(e) => { e.preventDefault(); pick(h.key) }}
              className={cn("flex cursor-pointer items-center gap-2 rounded-[5px] px-2.5 py-1.5", i === activeIdx ? "bg-accent text-foreground" : "text-foreground")}
            >
              <span className="font-mono font-medium">{h.label}</span>
              <span className="truncate text-muted-foreground">{h.name}</span>
            </li>
          ))}
        </ul>
      )}
    </form>
  )
}

/** Exactly two funds: the pair endpoint's detail, now with the weighted overlap beside the symbol-based one. */
function PairDetail({ d }: { d: FundCompare }) {
  const { t } = useI18n()
  return (
    <>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Stat label={t("compare.common")} value={d.common.length} />
        <Stat label={t("compare.overlap")} value={`${d.overlap_pct}%`} sub={t("compare.bySymbols")} />
        <Stat label={t("compare.overlapWeighted")} value={pct(d.overlap_pct_weighted)} sub={d.overlap_pct_weighted === null ? t("compare.weightedNa") : t("compare.weighted")} />
        <Stat label={t("compare.only", { f: d.a.code })} value={d.only_a.length} />
        <Stat label={t("compare.only", { f: d.b.code })} value={d.only_b.length} />
      </div>
      <div className="grid gap-5 lg:grid-cols-3">
        <List title={t("compare.bothIncreasing")} items={d.both_increasing} tone="pos" />
        <List title={t("compare.bothReducing")} items={d.both_reducing} tone="neg" />
        <Section title={t("compare.opposite")} hint={`${d.opposite.length}`}>
          <ul className="divide-y divide-border/60 text-sm">
            {d.opposite.map((o) => <li key={o.symbol} className="flex items-center gap-3 px-4 py-2"><Link to={`/stocks/${o.symbol}`} className="w-16 font-semibold hover:underline">{o.symbol}</Link><span className="font-mono text-xs">{d.a.code}</span><ActivityBadge value={o.a} /><span className="font-mono text-xs">{d.b.code}</span><ActivityBadge value={o.b} /></li>)}
            {d.opposite.length === 0 && <li className="px-4 py-6 text-muted-foreground">{t("common.none")}</li>}
          </ul>
        </Section>
      </div>
      <Section title={t("compare.commonPositions")} hint={`${d.a.code} · ${d.b.code}`}>
        <table className="w-full text-sm">
          <thead className="text-[11px] uppercase tracking-wider text-muted-foreground"><tr className="border-b border-border/60"><th className="px-4 py-2 text-left font-medium">{t("common.stock")}</th><th className="px-2 py-2 text-right font-medium">{d.a.code} {t("common.weight").toLowerCase()}</th><th className="px-2 py-2 text-left font-medium">{t("common.move").toLowerCase()}</th><th className="px-2 py-2 text-right font-medium">{d.b.code} {t("common.weight").toLowerCase()}</th><th className="px-4 py-2 text-left font-medium">{t("common.move").toLowerCase()}</th></tr></thead>
          <tbody>
            {d.common.map((c) => (
              <tr key={c.symbol} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                <td className="px-4 py-2"><Link to={`/stocks/${c.symbol}`} className="font-semibold hover:underline">{c.symbol}</Link></td>
                <td className="num px-2 py-2 text-right">{c.a_weight_pct?.toFixed(1) ?? "—"}%</td><td className="px-2 py-2">{c.a_move && <ActivityBadge value={c.a_move} />}</td>
                <td className="num px-2 py-2 text-right">{c.b_weight_pct?.toFixed(1) ?? "—"}%</td><td className="px-4 py-2">{c.b_move && <ActivityBadge value={c.b_move} />}</td>
              </tr>
            ))}
            {d.common.length === 0 && <tr><td colSpan={5} className="px-4 py-6 text-muted-foreground">{t("common.none")}</td></tr>}
          </tbody>
        </table>
      </Section>
    </>
  )
}

/** Three to six funds: the fund cards, the symmetric pairwise matrix and the stocks every fund holds with each fund's weight. */
function OverlapView({ d }: { d: FundOverlap }) {
  const { t } = useI18n()
  const codes = d.funds.map((f) => f.code)
  return (
    <>
      <div className="grid grid-cols-2 gap-3 md:grid-cols-3 lg:grid-cols-6">
        {d.funds.map((f) => (
          <div key={f.code} className="rounded-lg border border-border bg-card px-4 py-3">
            <Link to={`/funds/${f.code}`} className="font-mono text-lg font-semibold hover:underline">{f.code}</Link>
            <div className="truncate text-xs" title={f.name}>{f.name}</div>
            <div className="truncate text-[11px] text-muted-foreground" title={f.institution}>{f.institution}</div>
            <div className="num mt-1 text-[11px] text-muted-foreground">{t("compare.holdings", { n: f.holdings })} · {fmtDate(f.as_of)}</div>
          </div>
        ))}
      </div>
      <Section title={t("compare.pairwise")} hint={t("compare.pairwise.hint")}>
        <PairwiseMatrix codes={codes} pairs={d.pairwise} />
      </Section>
      <Section title={t("compare.commonAll")} hint={t("compare.commonAll.hint", { n: codes.length })}>
        {d.common_all.length === 0 ? (
          <div className="px-4 py-6 text-sm text-muted-foreground">{t("compare.noCommonAll")}</div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
                <tr className="border-b border-border/60">
                  <th className="px-4 py-2 text-left font-medium">{t("common.stock")}</th>
                  {codes.map((c) => <th key={c} className="num px-2 py-2 text-right font-medium">{c} {t("common.weight").toLowerCase()}</th>)}
                </tr>
              </thead>
              <tbody>
                {d.common_all.map((row) => (
                  <tr key={row.symbol} className="border-b border-border/40 last:border-0 hover:bg-accent/40">
                    <td className="px-4 py-2">
                      <Link to={`/stocks/${row.symbol}`} className="font-semibold hover:underline">{row.symbol}</Link>
                      {row.name && row.name !== row.symbol && <span className="ml-2 text-xs text-muted-foreground">{row.name}</span>}
                    </td>
                    {codes.map((c) => <td key={c} className="num whitespace-nowrap px-2 py-2 text-right">{pct(row.weights[c])}</td>)}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </Section>
    </>
  )
}

/**
 * Every unordered pair once in the API, shown twice here (the matrix is symmetric, so a row reads the same as a
 * column): the symbol-based overlap on top, the weighted one under it in muted type — a dash where either fund's
 * report carries no weights. The diagonal is empty.
 */
function PairwiseMatrix({ codes, pairs }: { codes: string[]; pairs: OverlapPair[] }) {
  const { t } = useI18n()
  const byPair = new Map<string, OverlapPair>()
  for (const p of pairs) { byPair.set(`${p.a}|${p.b}`, p); byPair.set(`${p.b}|${p.a}`, p) }
  return (
    <div className="overflow-x-auto">
      <table className="w-full text-sm">
        <thead className="text-[11px] uppercase tracking-wider text-muted-foreground">
          <tr className="border-b border-border/60">
            <th className="px-4 py-2 text-left font-medium" />
            {codes.map((c) => <th key={c} className="px-2 py-2 text-right font-mono font-medium">{c}</th>)}
          </tr>
        </thead>
        <tbody>
          {codes.map((a) => (
            <tr key={a} className="border-b border-border/40 last:border-0">
              <th scope="row" className="px-4 py-2 text-left font-mono font-medium">{a}</th>
              {codes.map((b) => {
                const p = a === b ? undefined : byPair.get(`${a}|${b}`)
                return (
                  <td key={b} className={cn("num whitespace-nowrap px-2 py-2 text-right align-top", a === b && "text-muted-foreground/50")} data-pair={a === b ? undefined : `${a}|${b}`}>
                    {a === b ? "·" : p ? (
                      <>
                        <div title={t("compare.bySymbols")}>{pct(p.overlap_pct_symbols, 0)}</div>
                        <div className="text-[11px] text-muted-foreground" title={p.overlap_pct_weighted === null ? t("compare.weightedNa") : t("compare.weighted")}>{pct(p.overlap_pct_weighted)}</div>
                      </>
                    ) : "—"}
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function Stat({ label, value, sub }: { label: string; value: React.ReactNode; sub?: string }) {
  return (
    <div className="rounded-lg border border-border bg-card px-4 py-3">
      <div className="text-[11px] uppercase tracking-wider text-muted-foreground">{label}</div>
      <div className="num mt-1 text-2xl font-semibold">{value}</div>
      {sub && <div className="text-[11px] text-muted-foreground">{sub}</div>}
    </div>
  )
}

function List({ title, items, tone }: { title: string; items: string[]; tone: "pos" | "neg" }) {
  const { t } = useI18n()
  return (
    <Section title={title} hint={`${items.length}`}>
      <ul className="flex flex-wrap gap-1.5 p-4">
        {items.map((s) => <Link key={s} to={`/stocks/${s}`} className={`rounded-sm border px-2 py-0.5 text-sm font-semibold ${tone === "pos" ? "border-positive/40 text-positive" : "border-negative/40 text-negative"}`}>{s}</Link>)}
        {items.length === 0 && <span className="text-sm text-muted-foreground">{t("common.none")}</span>}
      </ul>
    </Section>
  )
}
