import { act, renderHook } from "@testing-library/react"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { useCountUp } from "./motion"
import { mockMatchMedia } from "@/test/setup"

/** Drive requestAnimationFrame by hand so the easing is deterministic. */
function fakeRaf() {
  const queue: FrameRequestCallback[] = []
  let now = 1000
  vi.spyOn(performance, "now").mockImplementation(() => now)
  vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => { queue.push(cb); return queue.length })
  vi.stubGlobal("cancelAnimationFrame", () => {})
  return {
    step(ms: number) {
      now += ms
      const cbs = queue.splice(0)
      act(() => { for (const cb of cbs) cb(now) })
    },
    pending: () => queue.length,
  }
}

describe("useCountUp", () => {
  beforeEach(() => { mockMatchMedia(false) })
  afterEach(() => { vi.unstubAllGlobals(); vi.restoreAllMocks() })

  it("snaps on the first render", () => {
    fakeRaf()
    const { result } = renderHook(() => useCountUp(42))
    expect(result.current).toBe(42)
  })

  it("eases towards a new target when motion is allowed", () => {
    const raf = fakeRaf()
    const { result, rerender } = renderHook(({ v }) => useCountUp(v, 700), { initialProps: { v: 0 } })
    rerender({ v: 100 })
    expect(raf.pending()).toBe(1)
    raf.step(350)                          // halfway: ease-out cubic → 87.5
    expect(result.current).toBeCloseTo(87.5, 5)
    expect(raf.pending()).toBe(1)
    raf.step(700)                          // past the end: exactly the target, no further frames
    expect(result.current).toBe(100)
    expect(raf.pending()).toBe(0)
  })

  it("jumps straight to the target under prefers-reduced-motion", () => {
    mockMatchMedia(true)
    const raf = fakeRaf()
    const { result, rerender } = renderHook(({ v }) => useCountUp(v), { initialProps: { v: 0 } })
    rerender({ v: 100 })
    expect(result.current).toBe(100)
    expect(raf.pending()).toBe(0)           // nothing scheduled
  })

  it("clears to null and re-snaps when data goes away and comes back", () => {
    fakeRaf()
    const { result, rerender } = renderHook(({ v }) => useCountUp(v), { initialProps: { v: 10 as number | null } })
    rerender({ v: null })
    expect(result.current).toBeNull()
    rerender({ v: 55 })
    expect(result.current).toBe(55)
  })
})
