import { useQuery } from "@tanstack/react-query"
import { api, type Market } from "@/lib/api"
import { fmtDate } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { ConfidenceBadge, SignalBadge } from "./badges"
import { cn } from "@/lib/utils"

const DOT: Record<string, string> = { PERIOD: "bg-primary", EVENT: "bg-exact", SIGNAL: "bg-positive", SCORE: "bg-muted-foreground" }

export function Timeline({ symbol, market }: { symbol: string; market: Market }) {
  const q = useQuery({ queryKey: ["timeline", market, symbol], queryFn: () => api.timeline(market, symbol) })
  if (!q.data?.length) return null
  return (
    <Section title="Smart Money Timeline" hint="hikâye, kronolojik">
      <ol className="relative ml-4 border-l border-border py-2">
        {q.data.map((it, i) => (
          <li key={i} className="relative pl-5 pr-4 py-2">
            <span className={cn("absolute -left-[5px] top-3.5 size-2.5 rounded-full ring-2 ring-card", DOT[it.kind])} />
            <div className="flex flex-wrap items-center gap-2 text-sm">
              <span className="num w-24 text-xs text-muted-foreground">{fmtDate(it.date)}</span>
              {it.kind === "SIGNAL" && it.signal_type ? <SignalBadge type={it.signal_type} /> : <span className={cn(it.kind === "SCORE" && "text-muted-foreground")}>{it.title}</span>}
              {it.kind === "SIGNAL" && <span className="text-xs text-muted-foreground">{it.detail}</span>}
              {it.confidence && <ConfidenceBadge value={it.confidence} className="ml-auto" />}
            </div>
            {it.kind !== "SIGNAL" && it.detail && <div className="mt-0.5 pl-26 text-xs text-muted-foreground" style={{ paddingLeft: "6.5rem" }}>{it.detail}</div>}
          </li>
        ))}
      </ol>
    </Section>
  )
}
