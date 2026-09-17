import { useCallback, useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, type TxEvent } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { useI18n } from "@/lib/i18n"
import { Section } from "@/components/layout/Section"
import { EventRow } from "@/components/domain/EventRow"
import { ConfidenceBadge } from "@/components/domain/badges"
import { useNewIds } from "@/lib/motion"
import { useLiveConnected, useLiveEvent } from "@/lib/live"

/** Upper bound on rows kept in memory; the stream is unbounded, the page is not. */
const MAX_ROWS = 300

export function LivePage() {
  const { market } = useMarket()
  const { t } = useI18n()
  const initial = useQuery({ queryKey: ["events", market, 100], queryFn: () => api.events(market, 100) })
  const [live, setLive] = useState<TxEvent[]>([])
  const connected = useLiveConnected()

  // Rows arrive on the tab's shared stream (lib/live). Switching market drops what the previous stream accumulated —
  // those rows belong to the other feed.
  useEffect(() => { setLive([]) }, [market])
  const onTx = useCallback((data: Record<string, unknown>) => {
    const ev = data as unknown as TxEvent
    setLive((prev) => (prev.some((p) => p.id === ev.id) ? prev : [ev, ...prev].slice(0, MAX_ROWS)))
  }, [])
  useLiveEvent("transaction", onTx)

  const seen = new Set(live.map((e) => e.id))
  const rows = [...live, ...(initial.data ?? []).filter((e) => !seen.has(e.id))].slice(0, MAX_ROWS)
  const fresh = useNewIds(rows)

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className={`size-1.5 rounded-full ${connected ? "live-dot bg-positive text-positive" : "bg-negative"}`} /> {connected ? t("live.connected") : t("live.disconnected")}
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">{market === "TR" ? t("live.title.tr") : t("live.title.us")}</h1>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          {t("common.confidence")}: <ConfidenceBadge value="EXACT" /> <ConfidenceBadge value="GROUPED" /> <ConfidenceBadge value="INFERRED" />
        </div>
      </div>
      <Section title={t("live.section")} hint={`${rows.length} · ${t("live.newestFirst")}`}>
        {rows.map((ev) => <EventRow key={ev.id} ev={ev} market={market} fresh={fresh.has(ev.id)} />)}
        {rows.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">{t("live.none")}</div>}
      </Section>
    </div>
  )
}
