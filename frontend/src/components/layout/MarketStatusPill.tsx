import { useEffect, useState } from "react"
import { useQueryClient } from "@tanstack/react-query"
import type { Market } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { fmtInZone, marketStateDetail, marketStateLabel, useQuotes } from "@/lib/quotes"
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
 * `next_change_at`. The state shown is always the one the server sent: once `next_change_at` passes on this clock
 * the countdown is dropped (it would read 0) and the quotes query is invalidated once for that schedule; the regular
 * 60 s refetch then picks up the new state as soon as the backend's own 60 s cache turns over. Nothing while loading.
 */
export function MarketStatusPill({ market, className, tag = false }: { market: Market; className?: string; tag?: boolean }) {
  const { t, lang } = useI18n()
  const qc = useQueryClient()
  const q = useQuotes()
  const now = useNow(30_000)
  const status = q.data?.markets?.[market]
  const nextAt = status?.next_change_at
  const passed = nextAt ? Date.parse(nextAt) <= now : false
  useEffect(() => {
    if (passed) qc.invalidateQueries({ queryKey: ["quotes"] })
  }, [passed, nextAt, qc])
  if (!status) return null
  const state = status.state
  const label = marketStateLabel(market, state, t)
  const detail = passed ? null : marketStateDetail(status, now, t, lang)
  return (
    <div
      role="status"
      aria-label={`${t("market.status.label")} ${market}`}
      title={`${label} · ${fmtInZone(status.next_change_at, status.tz, lang)}`}
      className={cn("inline-flex h-7 shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border border-border bg-card px-2.5 text-[11px] leading-none", className)}
    >
      {tag && <span className="rounded-sm bg-muted px-1 py-px font-mono text-[9px] tracking-wider text-muted-foreground">{market}</span>}
      <span className={cn("size-1.5 rounded-full", DOT[state], state === "open" && "live-dot text-positive")} aria-hidden />
      <span className="font-medium text-foreground">{label}</span>
      {detail && <span className="num text-muted-foreground">· {detail}</span>}
    </div>
  )
}
