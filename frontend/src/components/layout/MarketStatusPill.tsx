import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import type { Market } from "@/lib/api"
import { fmtDateTime } from "@/lib/format"
import { useI18n } from "@/lib/i18n"
import { marketStateDetail, marketStateLabel, nextMarketState, useQuotes } from "@/lib/quotes"
import { cn } from "@/lib/utils"

/** Current time, re-read every `ms` — the countdown ticks without a network call. */
export function useNow(ms = 30_000): number {
  const [now, setNow] = useState(() => Date.now())
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), ms)
    return () => clearInterval(id)
  }, [ms])
  return now
}

const DOT: Record<"open" | "closed" | "pre" | "post", string> = {
  open: "bg-positive",
  pre: "bg-warning",
  post: "bg-warning",
  closed: "bg-muted-foreground/50",
}

/**
 * "BIST açık · kapanışa 1s 12dk" / "Kapalı · 10:00'da açılır" — state from /quotes, countdown computed here from
 * `next_change_at`. Once that moment passes the pill shows the state the market just entered (without a countdown,
 * whose end it does not know) and refetches on every tick until the server answers with the new schedule — the
 * backend caches for 60 s, so the first refetch may still carry the old one. Nothing while loading.
 */
export function MarketStatusPill({ market, className, tag = false }: { market: Market; className?: string; tag?: boolean }) {
  const { t, lang } = useI18n()
  const qc = useQueryClient()
  const q = useQuotes()
  const now = useNow(30_000)
  const status = q.data?.markets?.[market]
  const passed = status ? Date.parse(status.next_change_at) <= now : false
  useEffect(() => {
    if (passed) qc.invalidateQueries({ queryKey: ["quotes"] })
  }, [passed, now, qc])
  if (!status) return null
  const state = passed ? nextMarketState(market, status.state) : status.state
  const label = marketStateLabel(market, state, t)
  const detail = passed ? null : marketStateDetail(status, now, t, lang)
  return (
    <div
      role="status"
      aria-label={`${t("market.status.label")} ${market}`}
      title={`${label} · ${fmtDateTime(status.next_change_at)}`}
      className={cn("inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-card px-2.5 text-[11px] leading-none", className)}
    >
      {tag && <span className="rounded-sm bg-muted px-1 py-px font-mono text-[9px] tracking-wider text-muted-foreground">{market}</span>}
      <span className={cn("size-1.5 rounded-full", DOT[state], state === "open" && "live-dot text-positive")} aria-hidden />
      <span className="font-medium text-foreground">{label}</span>
      {detail && <span className="num text-muted-foreground">· {detail}</span>}
    </div>
  )
}
