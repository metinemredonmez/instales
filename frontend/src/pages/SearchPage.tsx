import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { Link, useSearchParams } from "react-router-dom"
import { ExternalLink, Search } from "lucide-react"
import { api, type Market, type TextHit, type TextKind } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n } from "@/lib/i18n"
import type { Key } from "@/i18n/tr"
import { fmtDate } from "@/lib/format"
import { Skeleton } from "@/components/ui/skeleton"
import { cn } from "@/lib/utils"

const KINDS: TextKind[] = ["disclosure", "filing", "news", "note"]
/** EDGAR filings exist for US instruments only: the TR market has no "filing" rows, so it gets no chip either. */
const kindsFor = (market: Market): TextKind[] => (market === "TR" ? KINDS.filter((k) => k !== "filing") : KINDS)
const MARKET_NAME: Record<Market, Key> = { TR: "market.name.TR", US: "market.name.US" }
// The API's bounds on q: shorter is refused, longer is cut here before it is sent.
const MIN_CHARS = 2
const MAX_CHARS = 128
const DEBOUNCE_MS = 300
/** The API caps `total` here, so a count at the cap reads as "at least". */
const TOTAL_CAP = 500
/** Page sizes: the first answer, then one "show more" up to the API's maximum; beyond that the count says to narrow the query. */
const LIMIT = 20
const LIMIT_MAX = 50
/** Symbol chips shown per hit; a portfolio report or a 13F carries up to 60, the hit's own page lists them all. */
const CHIPS = 6

/** `?kinds=news,note` → the picked kinds in the API's order; unknown names (and "filing" in TR) are dropped, none means every kind. */
const parseKinds = (csv: string | null, kinds: TextKind[]): TextKind[] => {
  const picked = (csv ?? "").split(",")
  return kinds.filter((k) => picked.includes(k))
}

/**
 * /search?q=&kinds= — full-text search over the active market's disclosures, filings (US), news and AI notes. The URL
 * is the state: the box writes `q` after a 300 ms pause (replace, so history does not collect keystrokes) and the
 * kind chips write `kinds`; the header box and the back button change the URL and the page follows. The list holds
 * the first 20 hits and one "show more" takes it to the API's 50.
 */
export function SearchPage() {
  const { market } = useMarket()
  const { t } = useI18n()
  const [params, setParams] = useSearchParams()
  const urlQ = (params.get("q") ?? "").trim().slice(0, MAX_CHARS)
  const allKinds = kindsFor(market)
  const kinds = parseKinds(params.get("kinds"), allKinds)
  const [text, setText] = useState(urlQ)
  // "Show more" is remembered for one query + filter + market; any change starts again at the first page.
  const scope = `${market}|${urlQ}|${kinds.join(",")}`
  const [more, setMore] = useState<string | null>(null)
  const limit = more === scope ? LIMIT_MAX : LIMIT
  const marketName = `${t(MARKET_NAME[market])} (${market})`

  // The URL changed under us: the box follows — unless it already says the same, so a trailing space survives typing.
  useEffect(() => { setText((cur) => (cur.trim().slice(0, MAX_CHARS) === urlQ ? cur : urlQ)) }, [urlQ])
  useEffect(() => {
    const next = text.trim().slice(0, MAX_CHARS)
    if (next === urlQ) return
    const id = setTimeout(() => setParams((p) => { if (next) p.set("q", next); else p.delete("q"); return p }, { replace: true }), DEBOUNCE_MS)
    return () => clearTimeout(id)
  }, [text, urlQ, setParams])

  /** null = "all": clears the picks; a kind toggles, and picking every kind is the same as none. */
  const toggleKind = (k: TextKind | null) => setParams((p) => {
    const next = k === null ? [] : kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k]
    if (next.length === 0 || next.length === allKinds.length) p.delete("kinds")
    else p.set("kinds", allKinds.filter((x) => next.includes(x)).join(","))
    return p
  })

  const enabled = urlQ.length >= MIN_CHARS
  const q = useQuery({
    queryKey: ["search-text", market, urlQ, kinds.join(","), limit],
    queryFn: () => api.searchText(market, urlQ, kinds.length ? kinds : null, limit),
    enabled,
    placeholderData: (prev) => prev,
  })
  const d = q.data

  return (
    <div className="space-y-5">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">{t("search.title")}</h1>
        <p className="text-sm text-muted-foreground">{market === "TR" ? t("search.sub.tr") : t("search.sub.us")}</p>
      </div>
      <div className="relative">
        <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
        <input
          value={text}
          onChange={(e) => setText(e.target.value)}
          autoFocus
          maxLength={MAX_CHARS}
          aria-label={t("search.input")}
          placeholder={t("search.pagePh")}
          className="h-10 w-full rounded-md border border-input bg-card pl-10 pr-3 text-sm outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring/40"
        />
      </div>
      <div role="group" aria-label={t("search.kindsLabel")} className="flex flex-wrap items-center gap-1.5">
        <Chip active={kinds.length === 0} onClick={() => toggleKind(null)}>{t("common.all")}</Chip>
        {allKinds.map((k) => <Chip key={k} active={kinds.includes(k)} onClick={() => toggleKind(k)}>{t(`search.kinds.${k}`)}</Chip>)}
      </div>

      {!enabled ? (
        <p className="text-sm text-muted-foreground">{t("search.minChars", { n: MIN_CHARS })}</p>
      ) : q.isError ? (
        <div className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground">{t("search.error")}</div>
      ) : !d || (q.isPlaceholderData && d.hits.length === 0) ? (
        // First load, or a new query with nothing on screen worth keeping dimmed (the previous one had no hits).
        <div className="space-y-2" aria-busy>{[0, 1, 2, 3, 4].map((i) => <Skeleton key={i} className="h-16" />)}</div>
      ) : d.hits.length === 0 ? (
        <div className="rounded-lg border border-dashed border-border p-10 text-center text-sm text-muted-foreground">{t("search.empty", { q: d.q, market: marketName })}</div>
      ) : (
        <section className={cn("rise rounded-lg border border-border bg-card", q.isPlaceholderData && "opacity-60 transition-opacity")}>
          <div className="border-b border-border/70 px-4 py-2 text-xs text-muted-foreground">
            {t("search.total", { n: d.total >= TOTAL_CAP ? `${TOTAL_CAP}+` : d.total })}
            {d.total > d.hits.length && ` · ${t("search.showingFirst", { n: d.hits.length })}`}
          </div>
          <ul className="divide-y divide-border/60">{d.hits.map((h) => <HitRow key={`${h.kind}:${h.id}`} h={h} />)}</ul>
          {d.total > d.hits.length && limit < LIMIT_MAX && (
            <div className="border-t border-border/70 px-4 py-2">
              <button type="button" onClick={() => setMore(scope)} disabled={q.isFetching} className="text-xs font-medium text-primary hover:underline disabled:opacity-60">{t("search.showMore")}</button>
            </div>
          )}
        </section>
      )}
    </div>
  )
}

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button type="button" aria-pressed={active} onClick={onClick} className={cn("rounded-md border px-2.5 py-1 text-xs font-medium transition", active ? "border-primary/40 bg-primary/10 text-foreground" : "border-border text-muted-foreground hover:text-foreground")}>
      {children}
    </button>
  )
}

/**
 * One hit: kind chip, the title (in-app route first, else the source page, else plain — an AI note has no page of its
 * own), snippet, date · source, up to CHIPS symbol chips. A superseded disclosure is marked and dimmed.
 */
function HitRow({ h }: { h: TextHit }) {
  const { t } = useI18n()
  const title = h.link
    ? <Link to={h.link} className="font-medium hover:underline">{h.title}</Link>
    : h.url
      ? <a href={h.url} target="_blank" rel="noopener noreferrer" className="font-medium hover:underline">{h.title}</a>
      : <span className="font-medium">{h.title}</span>
  const shown = h.symbols.slice(0, CHIPS)
  return (
    <li className={cn("space-y-1 px-4 py-3 text-sm", h.superseded && "opacity-70")}>
      <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
        <span className="shrink-0 rounded-sm bg-muted px-1 py-px font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{t(`search.kind.${h.kind}`)}</span>
        {h.superseded && <span className="shrink-0 rounded-sm border border-warning/40 bg-warning/10 px-1 py-px text-[10px] font-medium uppercase tracking-wider text-foreground">{t("search.superseded")}</span>}
        {title}
      </div>
      {h.snippet && <Snippet text={h.snippet} />}
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs text-muted-foreground">
        <span className="num">{fmtDate(h.date)}</span>
        <span aria-hidden>·</span>
        {h.link && h.url ? (
          <a href={h.url} target="_blank" rel="noopener noreferrer" title={t("search.source")} className="inline-flex items-center gap-1 hover:text-foreground">{h.source} <ExternalLink className="size-3" /></a>
        ) : (
          <span>{h.source}</span>
        )}
        {shown.map((s) => (
          <Link key={s} to={`/stocks/${s}`} className="rounded-sm border border-border px-1.5 py-0.5 font-mono text-[11px] text-foreground hover:bg-accent/40">{s}</Link>
        ))}
        {h.symbols.length > CHIPS && <span className="text-[11px] text-muted-foreground">{t("common.more", { n: h.symbols.length - CHIPS })}</span>}
      </div>
    </li>
  )
}

/** The API wraps matched terms in «»; each becomes a <mark>, the rest stays plain text. */
function Snippet({ text }: { text: string }) {
  const parts = text.split(/«([^»]*)»/)
  return (
    <p className="text-muted-foreground">
      {parts.map((part, i) => (i % 2 ? <mark key={i} className="rounded-sm bg-warning/30 px-0.5 text-foreground">{part}</mark> : part))}
    </p>
  )
}
