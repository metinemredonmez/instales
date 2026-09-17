import { useQuery } from "@tanstack/react-query"
import { api, type Market } from "@/lib/api"
import { fmtDate } from "@/lib/format"
import { providerLabel } from "@/lib/quotes"
import { cn } from "@/lib/utils"
import { useI18n, type T } from "@/lib/i18n"
import type { Key } from "@/i18n/tr"

// /freshness names its sources and cadences in English (services/analytics.data_freshness); the UI speaks the user's language.
const SOURCE: Record<string, Key> = {
  "KAP transaction disclosures": "fresh.src.kapTx",
  "KAP portfolio reports": "fresh.src.kapReports",
  "KAP insider filings": "fresh.src.kapInsiders",
  "Market prices": "fresh.src.prices",
  "SEC 13F": "fresh.src.sec13f",
  "SEC Form 4": "fresh.src.secForm4",
}
const CADENCE: Record<string, Key> = {
  "Same-day": "fresh.cad.sameDay",
  "Monthly snapshot": "fresh.cad.monthly",
  "Every 30 min on trading days": "fresh.cad.every30",
  "Quarterly, up to 45 days after quarter end": "fresh.cad.quarterly",
  "Within two business days of the trade": "fresh.cad.twoDays",
}
const DELAY: Record<string, Key> = { realtime: "fresh.delay.realtime", delayed: "fresh.delay.delayed", eod: "fresh.delay.eod" }

/** The source's name in the UI language; a name the map does not know (a newer API) is shown as the API sent it. */
export function freshnessSource(source: string, t: T): string {
  const k = SOURCE[source]
  return k ? t(k) : source
}
/** Same for the cadence; "Daily (yahoo, delayed)" carries the provider and its delay, which are translated word by word. */
export function freshnessCadence(cadence: string, t: T): string {
  const k = CADENCE[cadence]
  if (k) return t(k)
  const m = /^Daily \(([^,]+), ([^)]+)\)$/.exec(cadence)
  if (m) return t("fresh.cad.daily", { p: m[1] === "yahoo" || m[1] === "matriks" ? providerLabel(m[1], t) : m[1], d: DELAY[m[2]] ? t(DELAY[m[2]]) : m[2] })
  return cadence
}

/** Source freshness — the user must know how old each number is before trusting it. */
export function FreshnessBar({ market }: { market: Market }) {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["freshness", market], queryFn: () => api.freshness(market), refetchInterval: 60_000 })
  if (!q.data) return null
  return (
    <div className="flex flex-wrap items-center gap-2 text-[11px]">
      <span className="uppercase tracking-wider text-muted-foreground">{t("fresh.label")}</span>
      {q.data.map((f) => (
        <span key={f.source} title={freshnessCadence(f.cadence, t)} className={cn("inline-flex items-center gap-1.5 rounded-sm border px-1.5 py-0.5", f.delayed ? "border-warning/40 text-warning" : "border-positive/40 text-positive")}>
          <span className={cn("size-1.5 rounded-full", f.delayed ? "bg-warning" : "bg-positive", q.isFetching && "animate-pulse")} />
          {freshnessSource(f.source, t)} <span className="text-muted-foreground">· {f.last ? fmtDate(f.last) : "—"}{f.delayed ? ` · ${t("fresh.delayed")}` : ""}</span>
        </span>
      ))}
    </div>
  )
}
