import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { useState } from "react"
import { ExternalLink, Pin, X } from "lucide-react"
import { api, type Market, type NewsItem, type TxEvent } from "@/lib/api"
import { fmtDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import { ConfidenceBadge } from "./badges"
import { useI18n, type Lang } from "@/lib/i18n"
import { locale } from "@/lib/format"

type Item = { key: string; kind: "news" | "kap"; time: string; news?: NewsItem; event?: TxEvent }

/**
 * Language the headlines are published in. News rows carry no language column; the backend feeds are per market
 * (ingestion/news.py DEFAULT_FEEDS + NewsAPI language): BIST → Turkish sources, Global → English sources.
 * Headlines are never translated — when this differs from the UI language the strip says so instead of pretending.
 */
const SOURCE_LANG: Record<Market, Lang> = { TR: "tr", US: "en" }

/** Scrolling strip: newest headlines and disclosed institutional moves. Hover pauses and opens a detail card. */
export function NewsTicker({ market }: { market: Market }) {
  const { lang, t } = useI18n()
  const sourceLang = SOURCE_LANG[market]
  const foreign = sourceLang !== lang // headlines are not in the UI language (e.g. EN UI + BIST feeds)
  const sourceLangNote = t("ticker.sourceLangNote", { lang: t(sourceLang === "tr" ? "ticker.langTr" : "ticker.langEn") })
  const news = useQuery({ queryKey: ["news", market], queryFn: () => api.news(market, undefined, 30), refetchInterval: 120_000 })
  const events = useQuery({ queryKey: ["events", market, 8], queryFn: () => api.events(market, 8), refetchInterval: 60_000 })
  const [open, setOpen] = useState<Item | null>(null)
  const [pinned, setPinned] = useState(false)
  const show = (it: Item) => { if (!pinned) setOpen(it) }
  const leave = () => { if (!pinned) setOpen(null) }
  const close = () => { setPinned(false); setOpen(null) }
  const items: Item[] = [
    ...(events.data ?? []).map((e) => ({ key: `e${e.id}`, kind: "kap" as const, time: e.published_at, event: e })),
    ...(news.data ?? []).map((n) => ({ key: `n${n.id}`, kind: "news" as const, time: n.published_at, news: n })),
  ].sort((a, b) => b.time.localeCompare(a.time))
  if (items.length === 0) return null
  const loop = [...items, ...items]

  return (
    <div className="border-b border-border/70 bg-card/60 text-xs" onMouseLeave={leave}>
      <div className="group relative overflow-hidden">
        <div className="absolute left-0 top-0 z-10 flex h-full items-center bg-card px-3 font-semibold uppercase tracking-wider text-muted-foreground">
          <span className="mr-1.5 size-1.5 animate-pulse rounded-full bg-positive" /> {t("ticker.live")}
        </div>
        <div className={cn("ticker-track flex w-max items-center gap-8 py-2 pl-24", open && "[animation-play-state:paused]")} style={{ animationDuration: `${Math.max(120, items.length * 9)}s` }}>
          {loop.map((it, i) => (
            <TickerLink key={it.key + i} item={it} onEnter={() => show(it)} onPin={() => { setOpen(it); setPinned(true) }}>
              <span className="num text-muted-foreground">{fmtDateTime(it.time).split(" ").slice(-1)[0]}</span>
              {it.kind === "kap" && it.event ? (
                <span className={it.event.net_nominal > 0 ? "font-medium text-positive" : "font-medium text-negative"}>
                  KAP · {it.event.symbol} · {it.event.institution.split(" ")[0]} {it.event.net_nominal > 0 ? t("ticker.buy") : t("ticker.sell")} {Math.abs(it.event.net_nominal).toLocaleString(locale())} {t("common.lot")}
                </span>
              ) : (
                <span>
                  {it.news?.ai?.sentiment === "positive" && <span className="mr-1 text-positive">▲</span>}
                  {it.news?.ai?.sentiment === "negative" && <span className="mr-1 text-negative">▼</span>}
                  {foreign && <span className="mr-1.5 rounded-sm bg-muted px-1 py-px font-mono text-[10px] tracking-wider text-muted-foreground" title={sourceLangNote}>{sourceLang.toUpperCase()}</span>}
                  <span className="text-muted-foreground">{it.news?.source}:</span> {it.news?.title}
                </span>
              )}
              <span className="text-border">|</span>
            </TickerLink>
          ))}
        </div>
      </div>

      {open && (
        <div className="mx-auto max-w-[1600px] px-4">
        <div className={cn("rise relative my-2 overflow-hidden rounded-lg border border-border bg-popover p-4 pr-32 text-sm shadow-xl border-l-4",
          open.kind === "kap" ? (open.event && open.event.net_nominal > 0 ? "border-l-positive" : "border-l-negative")
          : open.news?.ai?.sentiment === "positive" ? "border-l-positive" : open.news?.ai?.sentiment === "negative" ? "border-l-negative" : "border-l-primary/60")}>
          <div className="absolute right-2 top-2 flex items-center gap-1 text-muted-foreground">
            {pinned ? <span className="inline-flex items-center gap-1 text-[10px] uppercase tracking-wider"><Pin className="size-3" /> {t("ticker.pinned")}</span> : <span className="text-[10px] uppercase tracking-wider">{t("ticker.pinHint")}</span>}
            <button onClick={close} aria-label={t("common.close")} className="rounded p-1 hover:bg-accent hover:text-foreground"><X className="size-4" /></button>
          </div>
          {open.kind === "news" && open.news ? (
            <>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="font-semibold text-primary">{open.news.source}</span><span>·</span><span className="num">{fmtDateTime(open.news.published_at)}</span>
                {open.news.ai?.sentiment === "positive" && <span className="rounded-sm border border-positive/40 bg-positive/10 px-1.5 py-0.5 text-[10px] font-semibold text-positive">▲ {lang === "tr" ? "olumlu" : "positive"}</span>}
                {open.news.ai?.sentiment === "negative" && <span className="rounded-sm border border-negative/40 bg-negative/10 px-1.5 py-0.5 text-[10px] font-semibold text-negative">▼ {lang === "tr" ? "olumsuz" : "negative"}</span>}
                {open.news.ai && open.news.ai.relevance > 0 && (
                  <span className="inline-flex items-center gap-1.5" title={`${t("ticker.relevance")} ${open.news.ai.relevance}/100`}>
                    <span className="text-[10px] uppercase tracking-wider">{t("ticker.relevance")}</span>
                    <span className="h-1 w-14 overflow-hidden rounded-full bg-muted"><span className="bar-anim block h-full rounded-full bg-primary" style={{ width: `${open.news.ai.relevance}%` }} /></span>
                    <span className="num">{open.news.ai.relevance}</span>
                  </span>
                )}
              </div>
              <div className="mt-1.5 text-[15px] font-semibold leading-snug">{open.news.title}</div>
              {foreign && <div className="mt-1 text-[10px] uppercase tracking-wider text-muted-foreground">{sourceLangNote}</div>}
              {lang === "tr" && open.news.ai?.summary_tr && <p className="mt-1.5 text-muted-foreground">{open.news.ai.summary_tr} <span className="text-[10px] uppercase tracking-wider">· {t("ticker.aiSummary")}</span></p>}
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                {open.news.symbols.map((s) => <Link key={s} to={`/stocks/${s}`} className="rounded-sm border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-xs font-semibold text-primary hover:bg-primary/20">{s}</Link>)}
                {open.news.tags.map((tg) => <span key={tg} className="rounded-sm border border-border bg-card px-1.5 py-0.5 text-xs text-muted-foreground">{tg}</span>)}
                {open.news.ai?.sector && <span className="rounded-sm border border-warning/40 bg-warning/10 px-1.5 py-0.5 text-xs text-warning">{open.news.ai.sector}</span>}
                <a href={open.news.url} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 text-xs text-primary hover:underline">{t("ticker.source")} <ExternalLink className="size-3" /></a>
              </div>
            </>
          ) : open.event ? (
            <>
              <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                <span className="font-semibold text-primary">KAP #{open.event.source.id}</span><span>·</span><span className="num">{fmtDateTime(open.event.published_at)}</span><span>·</span><ConfidenceBadge value={open.event.confidence} />
              </div>
              <div className="mt-1.5 text-[15px] font-semibold leading-snug">{open.event.institution} → <Link to={`/stocks/${open.event.symbol}`} className="text-primary hover:underline">{open.event.symbol}</Link></div>
              <div className={cn("mt-1 num font-medium", open.event.net_nominal > 0 ? "text-positive" : "text-negative")}>{open.event.net_nominal > 0 ? "+" : ""}{open.event.net_nominal.toLocaleString(locale())} {t("common.lot")} <span className="font-normal text-muted-foreground">· {t("common.funds").toLowerCase()}: {open.event.funds.join(", ") || "—"}</span></div>
              <div className="mt-3 flex gap-3 text-xs"><Link to={`/stocks/${open.event.symbol}`} className="text-primary hover:underline">{t("ticker.stockPage")} →</Link>{open.event.source.uri && <a href={open.event.source.uri} target="_blank" rel="noreferrer" className="text-muted-foreground hover:underline">{t("ticker.kapDisclosure")}</a>}</div>
            </>
          ) : null}
        </div>
        </div>
      )}
    </div>
  )
}

function TickerLink({ item, onEnter, onPin, children }: { item: Item; onEnter: () => void; onPin: () => void; children: React.ReactNode }) {
  const { t } = useI18n()
  const cls = "inline-flex items-center gap-2 whitespace-nowrap text-left hover:underline"
  const pin = (e: React.MouseEvent) => { e.preventDefault(); onPin() }
  if (item.kind === "news" && item.news) {
    // plain click = open the source in a new tab; the detail card pins on middle/ctrl-click or via its own link
    return <a href={item.news.url} target="_blank" rel="noreferrer" onMouseEnter={onEnter} onFocus={onEnter} onContextMenu={pin} className={cls} title={t("ticker.linkTitle")}>{children}</a>
  }
  if (item.event) {
    return <Link to={`/stocks/${item.event.symbol}`} onMouseEnter={onEnter} onFocus={onEnter} onContextMenu={pin} className={cls}>{children}</Link>
  }
  return <span className={cls}>{children}</span>
}
