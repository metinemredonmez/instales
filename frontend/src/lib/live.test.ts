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
