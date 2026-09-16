import { useQuery } from "@tanstack/react-query"
import { api, type Market } from "@/lib/api"
import { fmtDate } from "@/lib/format"
import { cn } from "@/lib/utils"

/** Source freshness — the user must know how old each number is before trusting it. */
export function FreshnessBar({ market }: { market: Market }) {
  const q = useQuery({ queryKey: ["freshness", market], queryFn: () => api.freshness(market) })
  if (!q.data) return null
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px]">
      <span className="uppercase tracking-wider text-muted-foreground">Veri tazeliği</span>
      {q.data.map((f) => (
        <span key={f.source} title={f.cadence} className={cn("inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5", f.delayed ? "border-warning/40 text-warning" : "border-positive/40 text-positive")}>
          <span className={cn("size-1.5 rounded-full", f.delayed ? "bg-warning" : "bg-positive")} />
          {f.source} <span className="text-muted-foreground">· {f.last ? fmtDate(f.last) : "—"}{f.delayed ? " · gecikmeli" : ""}</span>
        </span>
      ))}
    </div>
  )
}
