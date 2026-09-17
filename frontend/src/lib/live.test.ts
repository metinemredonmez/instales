import { describe, expect, it, vi } from "vitest"
import { QueryClient } from "@tanstack/react-query"
import { INVALIDATIONS, applyLiveEvent, connectLive, onLive } from "./live"

vi.mock("./api", () => ({
  api: {
    ticket: () => Promise.resolve({ ticket: "t1", ttl_seconds: 300 }),
    eventStreamUrl: (market: string, ticket: string, after?: number | null) => `/events/stream?market=${market}&ticket=${ticket}${after != null ? `&after=${after}` : ""}`,
  },
}))

class FakeSource {
  static instances: FakeSource[] = []
  listeners = new Map<string, ((e: MessageEvent) => void)[]>()
  onopen: (() => void) | null = null
  onerror: (() => void) | null = null
  closed = false
  url: string
  constructor(url: string) { this.url = url; FakeSource.instances.push(this) }
  addEventListener(type: string, fn: (e: MessageEvent) => void) { this.listeners.set(type, [...(this.listeners.get(type) ?? []), fn]) }
  close() { this.closed = true }
  fire(type: string, data: unknown, lastEventId = "") { this.listeners.get(type)?.forEach((fn) => fn({ data: JSON.stringify(data), lastEventId } as MessageEvent)) }
}

describe("live change events", () => {
  it("maps every change kind to the queries it makes stale", () => {
    const qc = new QueryClient()
    const spy = vi.spyOn(qc, "invalidateQueries")
    applyLiveEvent(qc, "compute", {})
    expect(spy.mock.calls.map((c) => c[0]?.queryKey)).toEqual(INVALIDATIONS.compute)
    spy.mockClear()
    applyLiveEvent(qc, "brief", { market: "US" })
    expect(spy.mock.calls.map((c) => c[0]?.queryKey)).toEqual([["ai-note"], ["ai-note", "US"]])
    spy.mockClear()
    applyLiveEvent(qc, "transaction", {})
    expect(spy.mock.calls.map((c) => c[0]?.queryKey)).toEqual([["events"]])
  })

  const quote = { key: "USDTRY", label: "USD/TRY", price: 41.2, change_pct: 0.1, currency: "TRY", updated_at: "2026-09-17T09:00:00Z", decimals: 4, bar_date: "2026-09-17", source: "yahoo", delayed: true }
  const markets = { TR: { state: "open", next_change_at: "2026-09-17T15:00:00Z", tz: "Europe/Istanbul" } }

  it("drops a quotes event straight into the cache (envelope stripped) instead of refetching", () => {
    const qc = new QueryClient()
    const invalidate = vi.spyOn(qc, "invalidateQueries")
    // Exactly what /events/stream sends: the /quotes payload under the change-event envelope (id, kind, market: null).
    applyLiveEvent(qc, "quotes", { id: 7, kind: "quotes", market: null, as_of: "2026-09-17T09:00:05Z", quotes: [quote], markets })
    expect(qc.getQueryData(["quotes"])).toStrictEqual({ as_of: "2026-09-17T09:00:05Z", quotes: [quote], markets })
    expect(invalidate).not.toHaveBeenCalled()
  })

  it("keeps the newer numbers when a replayed quotes event is older than what the tab holds", () => {
    const qc = new QueryClient()
    const t1 = { as_of: "2026-09-17T09:00:05+00:00", quotes: [{ ...quote, price: 41.1 }], markets }
    const t2 = { as_of: "2026-09-17T09:01:05+00:00", quotes: [{ ...quote, price: 41.2 }], markets }
    applyLiveEvent(qc, "quotes", { id: 8, kind: "quotes", market: null, ...t2 })
    // A reconnect replays what was missed, in id order; the 60 s poll (or a later event) may already be ahead of it.
    applyLiveEvent(qc, "quotes", { id: 7, kind: "quotes", market: null, ...t1 })
    expect(qc.getQueryData(["quotes"])).toStrictEqual(t2)
    applyLiveEvent(qc, "quotes", { id: 9, kind: "quotes", market: null, ...t2 })   // the same instant again: not newer
    expect(qc.getQueryData(["quotes"])).toStrictEqual(t2)
    const t3 = { as_of: "2026-09-17T09:02:05.250000+00:00", quotes: [{ ...quote, price: 41.3 }], markets }
    applyLiveEvent(qc, "quotes", { id: 10, kind: "quotes", market: null, ...t3 })
    expect(qc.getQueryData(["quotes"])).toStrictEqual(t3)
  })

  it("ignores an empty quotes payload so the strip is never blanked", () => {
    const qc = new QueryClient()
    const held = { as_of: "2026-09-17T09:00:05+00:00", quotes: [quote], markets }
    qc.setQueryData(["quotes"], held)
    applyLiveEvent(qc, "quotes", { id: 11, kind: "quotes", market: null, as_of: "2026-09-17T09:03:05+00:00", quotes: [], markets })
    expect(qc.getQueryData(["quotes"])).toStrictEqual(held)
    const empty = new QueryClient()
    applyLiveEvent(empty, "quotes", { id: 11, kind: "quotes", market: null, as_of: "2026-09-17T09:03:05+00:00", quotes: [], markets })
    expect(empty.getQueryData(["quotes"])).toBeUndefined()
  })

  it("connects with a ticket, fans events out to subscribers and reconnects from the last id", async () => {
    vi.useFakeTimers()
    const qc = new QueryClient()
    const seen: unknown[] = []
    const off = onLive("transaction", (d) => seen.push(d))
    const stop = connectLive("TR", qc, { retryMs: 1000, makeSource: (url) => new FakeSource(url) as unknown as EventSource })
    await vi.advanceTimersByTimeAsync(0)
    const first = FakeSource.instances.at(-1)!
    expect(first.url).toBe("/events/stream?market=TR&ticket=t1")
    first.onopen?.()
    first.fire("ready", { last_id: 0, live_id: 41 })
    first.fire("transaction", { id: 9, symbol: "ASELS" })
    first.fire("compute", { id: 42, kind: "compute" }, "42")
    expect(seen).toEqual([{ id: 9, symbol: "ASELS" }])
    first.onerror?.()  // dropped → reconnect after retryMs with the cursor
    expect(first.closed).toBe(true)
    await vi.advanceTimersByTimeAsync(1000)
    const second = FakeSource.instances.at(-1)!
    expect(second).not.toBe(first)
    expect(second.url).toBe("/events/stream?market=TR&ticket=t1&after=42")
    stop()
    off()
    expect(second.closed).toBe(true)
    vi.useRealTimers()
  })
})
