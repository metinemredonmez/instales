import { Link } from "react-router-dom"
import { ExternalLink } from "lucide-react"
import type { TxEvent } from "@/lib/api"
import { fmtDateTime, fmtLots } from "@/lib/format"
import { cn } from "@/lib/utils"
import { useI18n } from "@/lib/i18n"
import { ConfidenceBadge, Flow } from "./badges"

export function EventRow({ ev, compact, fresh }: { ev: TxEvent; compact?: boolean; fresh?: boolean }) {
  const { t } = useI18n()
  const buy = ev.net_nominal > 0
  return (
    <div className={cn("flex items-start gap-3 border-b border-border/60 px-4 py-3 last:border-0", compact && "py-2.5", fresh && (buy ? "slide-in flash-pos" : "slide-in flash-neg"))}>
      <div className={cn("mt-1 size-2 shrink-0 rounded-full", buy ? "bg-positive" : "bg-negative", fresh && (buy ? "live-dot text-positive" : "live-dot text-negative"))} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <Link to={`/stocks/${ev.symbol}`} className="font-semibold hover:underline">{ev.symbol}</Link>
          <span className={cn("text-xs font-medium", buy ? "text-positive" : "text-negative")}>{buy ? t("event.increase") : t("event.decrease")}</span>
          <ConfidenceBadge value={ev.confidence} />
          <span className="ml-auto text-xs text-muted-foreground num">{fmtDateTime(ev.published_at)}</span>
        </div>
        <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
          <span className="text-foreground/80">{ev.institution}</span>
          {ev.funds.length > 0 && (
            <span>
              {ev.funds.map((f) => (
                <Link key={f} to={`/funds/${f}`} className="mr-1 rounded bg-secondary px-1 py-0.5 font-mono text-[11px] text-foreground hover:underline">{f}</Link>
              ))}
              {ev.allocation === "UNKNOWN" && <span className="text-grouped">· {t("event.allocUnknown")}</span>}
            </span>
          )}
        </div>
        {!compact && (
          <div className="mt-1.5 flex flex-wrap items-center gap-x-4 text-xs">
            <span className={cn("num font-semibold", buy ? "text-positive" : "text-negative")}>{fmtLots(ev.net_nominal)} {t("common.lot")}</span>
            <Flow value={ev.net_value} />
            {ev.ownership_before_pct !== null && ev.ownership_after_pct !== null && (
              <span className="num text-muted-foreground">{ev.ownership_before_pct.toFixed(2)}% → {ev.ownership_after_pct.toFixed(2)}%</span>
            )}
            <a href={ev.source.uri ?? "#"} target="_blank" rel="noreferrer" className="ml-auto inline-flex items-center gap-1 text-muted-foreground hover:text-foreground">
              {ev.source.name} #{ev.source.id} <ExternalLink className="size-3" />
            </a>
          </div>
        )}
      </div>
    </div>
  )
}
