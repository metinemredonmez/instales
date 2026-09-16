import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { useState } from "react"
import { ExternalLink } from "lucide-react"
import { api, type Market, type NewsItem, type TxEvent } from "@/lib/api"
import { fmtDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"

type Item = { key: string; kind: "news" | "kap"; time: string; news?: NewsItem; event?: TxEvent }

/** Scrolling strip: newest headlines and disclosed institutional moves. Hover pauses and opens a detail card. */
export function NewsTicker({ market }: { market: Market }) {
  const news = useQuery({ queryKey: ["news", market], queryFn: () => api.news(market, undefined, 30), refetchInterval: 120_000 })
  const events = useQuery({ queryKey: ["events", market, 8], queryFn: () => api.events(market, 8), refetchInterval: 60_000 })
  const [open, setOpen] = useState<Item | null>(null)
  const items: Item[] = [
    ...(events.data ?? []).map((e) => ({ key: `e${e.id}`, kind: "kap" as const, time: e.published_at, event: e })),
    ...(news.data ?? []).map((n) => ({ key: `n${n.id}`, kind: "news" as const, time: n.published_at, news: n })),
  ].sort((a, b) => b.time.localeCompare(a.time))
  if (items.length === 0) return null
  const loop = [...items, ...items]

  return (
    <div className="relative border-b border-border/70 bg-card/60 text-xs" onMouseLeave={() => setOpen(null)}>
      <div className="group relative overflow-hidden">
        <div className="absolute left-0 top-0 z-10 flex h-full items-center bg-card px-3 font-semibold uppercase tracking-wider text-muted-foreground">
          <span className="mr-1.5 size-1.5 animate-pulse rounded-full bg-positive" /> Canlı
        </div>
        <div className={cn("ticker-track flex w-max items-center gap-8 py-2 pl-24", open && "[animation-play-state:paused]")} style={{ animationDuration: `${Math.max(120, items.length * 9)}s` }}>
          {loop.map((it, i) => (
            <button key={it.key + i} type="button" onMouseEnter={() => setOpen(it)} onFocus={() => setOpen(it)} className="inline-flex items-center gap-2 whitespace-nowrap text-left hover:underline">
              <span className="num text-muted-foreground">{fmtDateTime(it.time).split(" ").slice(-1)[0]}</span>
              {it.kind === "kap" && it.event ? (
                <span className={it.event.net_nominal > 0 ? "font-medium text-positive" : "font-medium text-negative"}>
                  KAP · {it.event.symbol} · {it.event.institution.split(" ")[0]} {it.event.net_nominal > 0 ? "alım" : "satım"} {Math.abs(it.event.net_nominal).toLocaleString("tr-TR")} lot
                </span>
              ) : (
                <span>
                  {it.news?.ai?.sentiment === "positive" && <span className="mr-1 text-positive">▲</span>}
                  {it.news?.ai?.sentiment === "negative" && <span className="mr-1 text-negative">▼</span>}
                  <span className="text-muted-foreground">{it.news?.source}:</span> {it.news?.title}
                </span>
              )}
              <span className="text-border">|</span>
            </button>
          ))}
        </div>
      </div>

      {open && (
        <div className="absolute left-4 top-full z-40 mt-1 w-[min(560px,calc(100vw-2rem))] rounded-lg border border-border bg-popover p-4 text-sm shadow-xl">
          {open.kind === "news" && open.news ? (
            <>
              <div className="flex items-center gap-2 text-xs text-muted-foreground">
                <span>{open.news.source}</span><span>·</span><span className="num">{fmtDateTime(open.news.published_at)}</span>
                {open.news.ai && open.news.ai.relevance > 0 && <span className="ml-auto rounded-sm border border-border px-1.5 py-0.5">ilgi {open.news.ai.relevance}/100</span>}
              </div>
              <div className="mt-1 font-medium">{open.news.title}</div>
              {open.news.ai?.summary_tr && <p className="mt-2 text-muted-foreground">{open.news.ai.summary_tr} <span className="text-[10px] uppercase tracking-wider">· AI özeti</span></p>}
              <div className="mt-3 flex flex-wrap items-center gap-1.5">
                {open.news.symbols.map((s) => <Link key={s} to={`/stocks/${s}`} className="rounded-sm border border-primary/40 bg-primary/10 px-1.5 py-0.5 text-xs font-semibold">{s}</Link>)}
                {open.news.tags.map((t) => <span key={t} className="rounded-sm border border-border px-1.5 py-0.5 text-xs text-muted-foreground">{t}</span>)}
                {open.news.ai?.sector && <span className="rounded-sm border border-border px-1.5 py-0.5 text-xs text-muted-foreground">{open.news.ai.sector}</span>}
                <a href={open.news.url} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 text-xs text-primary hover:underline">Kaynağa git <ExternalLink className="size-3" /></a>
              </div>
            </>
          ) : open.event ? (
            <>
              <div className="text-xs text-muted-foreground">KAP #{open.event.source.id} · <span className="num">{fmtDateTime(open.event.published_at)}</span> · {open.event.confidence}</div>
              <div className="mt-1 font-medium">{open.event.institution} → {open.event.symbol}</div>
              <div className={cn("mt-1 num", open.event.net_nominal > 0 ? "text-positive" : "text-negative")}>{open.event.net_nominal > 0 ? "+" : ""}{open.event.net_nominal.toLocaleString("tr-TR")} lot · fonlar: {open.event.funds.join(", ") || "—"}</div>
              <div className="mt-2 flex gap-3 text-xs"><Link to={`/stocks/${open.event.symbol}`} className="text-primary hover:underline">Hisse sayfası →</Link>{open.event.source.uri && <a href={open.event.source.uri} target="_blank" rel="noreferrer" className="text-muted-foreground hover:underline">KAP bildirimi</a>}</div>
            </>
          ) : null}
        </div>
      )}
    </div>
  )
}
