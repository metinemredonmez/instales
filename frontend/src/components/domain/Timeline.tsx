import { useQuery } from "@tanstack/react-query"
import { api, type Market, type TimelineItem } from "@/lib/api"
import { fmtDate, fmtLots } from "@/lib/format"
import { Section } from "@/components/layout/Section"
import { ConfidenceBadge, SignalBadge } from "./badges"
import { cn } from "@/lib/utils"
import { useI18n, type T } from "@/lib/i18n"

const DOT: Record<string, string> = { PERIOD: "bg-primary", EVENT: "bg-exact", SIGNAL: "bg-positive", SCORE: "bg-muted-foreground" }

// The API composes titles server-side (Turkish, "alım/satım" for events). Rebuild them here from the numbers so the
// wording is neutral ("position increase/decrease") and follows the UI language; anything unrecognised is shown as-is.
const EVENT_TITLE = /^(.*?):\s*(alım|satım|artış|azalış|increase|decrease)\s*([+\-−]?[\d.,]+)\s*$/i
const PERIOD_TITLE = /^(\d+)\s+fon artırdı\s*·\s*(\d+)\s+azalttı$/
const PERIOD_DETAIL = /^(\d+)\s+yeni\s*·\s*(\d+)\s+çıkış\s*·\s*net\s*([+\-−]?[\d.,]+)\s+lot$/
const SIGNAL_DETAIL = /^güç\s+(\d+)$/
const SCORE_TITLE = /^Smart Money Score\s*→\s*(\d+)$/
const num = (s: string) => Number(s.replace(/[.,]/g, "").replace("−", "-"))

export function timelineTitle(t: T, it: TimelineItem): string {
  if (it.kind === "EVENT") {
    const m = EVENT_TITLE.exec(it.title)
    if (m) {
      const n = num(m[3])
      return `${m[1]}: ${t(n >= 0 ? "event.increaseShort" : "event.decreaseShort")} ${fmtLots(n)}`
    }
  } else if (it.kind === "PERIOD") {
    const m = PERIOD_TITLE.exec(it.title)
    if (m) return t("timeline.period", { inc: m[1], red: m[2] })
  } else if (it.kind === "SCORE") {
    const m = SCORE_TITLE.exec(it.title)
    if (m) return t("timeline.score", { n: m[1] })
  }
  return it.title
}

export function timelineDetail(t: T, it: TimelineItem): string | null {
  if (!it.detail) return null
  if (it.kind === "PERIOD") {
    const m = PERIOD_DETAIL.exec(it.detail)
    if (m) return t("timeline.periodDetail", { n: m[1], x: m[2], q: fmtLots(num(m[3])) })
  } else if (it.kind === "SIGNAL") {
    const m = SIGNAL_DETAIL.exec(it.detail)
    if (m) return t("timeline.strength", { n: m[1] })
  }
  return it.detail
}

export function Timeline({ symbol, market }: { symbol: string; market: Market }) {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["timeline", market, symbol], queryFn: () => api.timeline(market, symbol) })
  if (!q.data?.length) return null
  return (
    <Section title="Smart Money Timeline" hint={t("timeline.hint")}>
      <ol className="relative ml-4 border-l border-border py-2">
        {q.data.map((it, i) => {
          const detail = timelineDetail(t, it)
          return (
            <li key={i} className="relative pl-5 pr-4 py-2">
              <span className={cn("absolute -left-[5px] top-3.5 size-2.5 rounded-full ring-2 ring-card", DOT[it.kind])} />
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="num w-24 text-xs text-muted-foreground">{fmtDate(it.date)}</span>
                {it.kind === "SIGNAL" && it.signal_type ? <SignalBadge type={it.signal_type} /> : <span className={cn(it.kind === "SCORE" && "text-muted-foreground")}>{timelineTitle(t, it)}</span>}
                {it.kind === "SIGNAL" && <span className="text-xs text-muted-foreground">{detail}</span>}
                {it.confidence && <ConfidenceBadge value={it.confidence} className="ml-auto" />}
              </div>
              {it.kind !== "SIGNAL" && detail && <div className="mt-0.5 pl-26 text-xs text-muted-foreground" style={{ paddingLeft: "6.5rem" }}>{detail}</div>}
            </li>
          )
        })}
      </ol>
    </Section>
  )
}
