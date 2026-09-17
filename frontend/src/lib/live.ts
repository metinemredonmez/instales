import { useEffect, useSyncExternalStore } from "react"
import { useQueryClient, type QueryClient } from "@tanstack/react-query"
import { api, type Market } from "./api"

/**
 * One live stream per tab (see backend /events/stream). Full KAP/SEC transaction rows arrive as `transaction`;
 * everything else is a small "something changed" event that we turn into query refetches, so the pages keep their
 * plain useQuery calls and only get faster. A dropped connection falls back to the pages' own polling until the
 * reconnect (fresh 5-minute ticket, `after=` cursor so nothing that fired meanwhile is lost).
 */
export type LiveKind = "transaction" | "notification" | "compute" | "news" | "brief" | "pipeline"
export type LiveHandler = (data: Record<string, unknown>) => void

const handlers = new Map<LiveKind, Set<LiveHandler>>()
let connected = false
const listeners = new Set<() => void>()
const setConnected = (v: boolean) => { if (connected !== v) { connected = v; listeners.forEach((l) => l()) } }

export function onLive(kind: LiveKind, fn: LiveHandler) {
  if (!handlers.has(kind)) handlers.set(kind, new Set())
  handlers.get(kind)!.add(fn)
  return () => { handlers.get(kind)?.delete(fn) }
}
const emit = (kind: LiveKind, data: Record<string, unknown>) => handlers.get(kind)?.forEach((fn) => fn(data))

/** Which cached queries each change event makes stale. Keys are prefixes — TanStack matches every longer key. */
export const INVALIDATIONS: Record<Exclude<LiveKind, "transaction">, unknown[][]> = {
  notification: [["notifications"]],
  compute: [["radar"], ["stock"], ["screener"], ["watchlist"], ["freshness"], ["signal-perf"], ["timeline"], ["series"], ["fund"], ["institution"], ["institutions"], ["compare"], ["events"]],
  news: [["news"]],
  brief: [["ai-note"]],
  pipeline: [["pipeline-status"], ["freshness"], ["admin"]],
}

export function applyLiveEvent(qc: QueryClient, kind: LiveKind, data: Record<string, unknown>) {
  if (kind === "transaction") { qc.invalidateQueries({ queryKey: ["events"] }); return }
  for (const key of INVALIDATIONS[kind] ?? []) qc.invalidateQueries({ queryKey: key })
  // A brief only concerns the tabs on that market; the note key is ["ai-note", market, "market", lang].
  if (kind === "brief" && typeof data.market === "string") qc.invalidateQueries({ queryKey: ["ai-note", data.market] })
}

const KINDS: LiveKind[] = ["transaction", "notification", "compute", "news", "brief", "pipeline"]

/** Opens the stream for `market`; returns a stop function. Exported for tests — the hook below is what the shell uses. */
export function connectLive(market: Market, qc: QueryClient, opts: { retryMs?: number; makeSource?: (url: string) => EventSource } = {}) {
  const retryMs = opts.retryMs ?? 5000
  const make = opts.makeSource ?? ((url: string) => new EventSource(url))
  let es: EventSource | null = null
  let timer: number | undefined
  let stopped = false
  let after: number | null = null
  const connect = async () => {
    try {
      const { ticket } = await api.ticket()
      if (stopped) return
      es = make(api.eventStreamUrl(market, ticket, after))
      es.onopen = () => setConnected(true)
      es.onerror = () => { setConnected(false); es?.close(); if (!stopped) timer = window.setTimeout(connect, retryMs) }
      es.addEventListener("ready", (e) => { const d = JSON.parse((e as MessageEvent).data); if (typeof d.live_id === "number") after = d.live_id })
      for (const kind of KINDS) {
        es.addEventListener(kind, (e) => {
          const me = e as MessageEvent
          const data = JSON.parse(me.data) as Record<string, unknown>
          if (me.lastEventId) after = Number(me.lastEventId)
          applyLiveEvent(qc, kind, data)
          emit(kind, data)
        })
      }
    } catch { if (!stopped) timer = window.setTimeout(connect, retryMs * 2) }
  }
  connect()
  return () => { stopped = true; es?.close(); window.clearTimeout(timer); setConnected(false) }
}

/** Mount once (AppShell). Reconnects when the market changes; the stream itself carries the market filter. */
export function useLiveStream(market: Market, enabled = true) {
  const qc = useQueryClient()
  useEffect(() => { if (!enabled) return; return connectLive(market, qc) }, [market, qc, enabled])
}

export const useLiveConnected = () => useSyncExternalStore((cb) => { listeners.add(cb); return () => listeners.delete(cb) }, () => connected)

/** Subscribe a component to one event kind for as long as it is mounted. */
export function useLiveEvent(kind: LiveKind, fn: LiveHandler) {
  useEffect(() => onLive(kind, fn), [kind, fn])
}
