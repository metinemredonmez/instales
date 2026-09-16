import { useEffect, useRef, useState } from "react"

const reduced = () => typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches

/** Animate a number towards `target` (ease-out, ~700 ms). First render snaps; later changes count up/down. */
export function useCountUp(target: number | null | undefined, ms = 700): number | null {
  const [value, setValue] = useState<number | null>(target ?? null)
  const from = useRef<number | null>(target ?? null)
  useEffect(() => {
    if (target === null || target === undefined) { setValue(null); from.current = null; return }
    const start = from.current
    if (start === null || reduced() || start === target) { setValue(target); from.current = target; return }
    let raf = 0
    const t0 = performance.now()
    const tick = (now: number) => {
      const p = Math.min(1, (now - t0) / ms)
      const e = 1 - Math.pow(1 - p, 3)
      setValue(start + (target - start) * e)
      if (p < 1) raf = requestAnimationFrame(tick)
      else from.current = target
    }
    raf = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(raf)
  }, [target, ms])
  return value
}

/** Returns a CSS class that flashes the element briefly when `value` changes (not on first render). */
export function useFlash(value: unknown, tone: "pos" | "neg" | "info" = "info"): string {
  const prev = useRef(value)
  const [cls, setCls] = useState("")
  useEffect(() => {
    if (prev.current === value) return
    prev.current = value
    if (reduced()) return
    const c = tone === "pos" ? "flash-pos" : tone === "neg" ? "flash-neg" : "flash"
    setCls("")  // restart the animation even if the same class is re-applied
    const id = requestAnimationFrame(() => setCls(c))
    const t = setTimeout(() => setCls(""), 1700)
    return () => { cancelAnimationFrame(id); clearTimeout(t) }
  }, [value, tone])
  return cls
}

/** Track ids seen so far; returns a Set of ids that are new in this render (for slide-in on live feeds). */
export function useNewIds<T extends { id: number | string }>(rows: T[] | undefined): Set<T["id"]> {
  const seen = useRef<Set<T["id"]> | null>(null)
  const fresh = new Set<T["id"]>()
  if (!rows) return fresh
  if (seen.current === null) { seen.current = new Set(rows.map((r) => r.id)); return fresh }  // first paint: nothing is "new"
  for (const r of rows) if (!seen.current.has(r.id)) { fresh.add(r.id); seen.current.add(r.id) }
  return fresh
}
