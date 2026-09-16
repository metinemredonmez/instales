import { useEffect, useState } from "react"
import { useQuery } from "@tanstack/react-query"
import { api, type TxEvent } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { Section } from "@/components/layout/Section"
import { EventRow } from "@/components/domain/EventRow"
import { ConfidenceBadge } from "@/components/domain/badges"

export function LivePage() {
  const { market } = useMarket()
  const initial = useQuery({ queryKey: ["events", market, 100], queryFn: () => api.events(market, 100) })
  const [live, setLive] = useState<TxEvent[]>([])
  const [connected, setConnected] = useState(false)

  useEffect(() => {
    const es = new EventSource(api.eventStreamUrl(market))
    es.onopen = () => setConnected(true)
    es.onerror = () => setConnected(false)
    es.addEventListener("transaction", (e) => setLive((prev) => [JSON.parse((e as MessageEvent).data) as TxEvent, ...prev]))
    return () => es.close()
  }, [market])

  const seen = new Set(live.map((e) => e.id))
  const rows = [...live, ...(initial.data ?? []).filter((e) => !seen.has(e.id))]

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-xs text-muted-foreground">
            <span className={`size-1.5 rounded-full ${connected ? "animate-pulse bg-positive" : "bg-negative"}`} /> {connected ? "SSE bağlı" : "bağlantı yok"}
          </div>
          <h1 className="mt-1 text-2xl font-semibold tracking-tight">Canlı KAP Radar</h1>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          Güven: <ConfidenceBadge value="EXACT" /> <ConfidenceBadge value="GROUPED" /> <ConfidenceBadge value="INFERRED" />
        </div>
      </div>
      <Section title="İşlem bildirimleri" hint={`${rows.length} · yeni gelenler üstte`}>
        {rows.map((ev) => <EventRow key={ev.id} ev={ev} />)}
        {rows.length === 0 && <div className="px-4 py-6 text-sm text-muted-foreground">Bildirim yok.</div>}
      </Section>
    </div>
  )
}
