import { useQuery } from "@tanstack/react-query"
import { Link } from "react-router-dom"
import { api, type Market } from "@/lib/api"
import { fmtDateTime } from "@/lib/format"

/** Scrolling strip: latest headlines and disclosed institutional moves, newest first. Headlines link out. */
export function NewsTicker({ market }: { market: Market }) {
  const news = useQuery({ queryKey: ["news", market], queryFn: () => api.news(market, undefined, 25), refetchInterval: 120_000 })
  const events = useQuery({ queryKey: ["events", market, 8], queryFn: () => api.events(market, 8), refetchInterval: 60_000 })
  const items: { key: string; kind: "news" | "kap"; text: string; href: string; external: boolean; time: string; tone?: "pos" | "neg" }[] = [
    ...(events.data ?? []).map((e) => ({
      key: `e${e.id}`, kind: "kap" as const, external: false, href: `/stocks/${e.symbol}`, time: e.published_at,
      text: `${e.symbol} · ${e.institution.split(" ")[0]} ${e.net_nominal > 0 ? "alım" : "satım"} ${Math.abs(e.net_nominal).toLocaleString("tr-TR")} lot`,
      tone: e.net_nominal > 0 ? ("pos" as const) : ("neg" as const),
    })),
    ...(news.data ?? []).map((n) => ({ key: `n${n.id}`, kind: "news" as const, external: true, href: n.url, time: n.published_at, text: `${n.source}: ${n.title}` })),
  ].sort((a, b) => b.time.localeCompare(a.time))
  if (items.length === 0) return null
  const loop = [...items, ...items]
  return (
    <div className="group relative overflow-hidden border-b border-border/70 bg-card/60 text-xs">
      <div className="absolute left-0 top-0 z-10 flex h-full items-center bg-card px-3 font-semibold uppercase tracking-wider text-muted-foreground">
        <span className="mr-1.5 size-1.5 animate-pulse rounded-full bg-positive" /> Canlı
      </div>
      <div className="ticker-track flex w-max items-center gap-8 py-2 pl-24 group-hover:[animation-play-state:paused]">
        {loop.map((it, i) => (
          <span key={it.key + i} className="inline-flex items-center gap-2 whitespace-nowrap">
            <span className="num text-muted-foreground">{fmtDateTime(it.time).split(" ").slice(-1)[0]}</span>
            {it.kind === "kap" ? (
              <Link to={it.href} className={it.tone === "pos" ? "font-medium text-positive hover:underline" : "font-medium text-negative hover:underline"}>KAP · {it.text}</Link>
            ) : (
              <a href={it.href} target="_blank" rel="noreferrer" className="hover:underline">{it.text}</a>
            )}
            <span className="text-border">|</span>
          </span>
        ))}
      </div>
    </div>
  )
}
