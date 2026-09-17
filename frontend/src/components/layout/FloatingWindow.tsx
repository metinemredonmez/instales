import { useEffect, useRef, type ReactNode } from "react"
import { cn } from "@/lib/utils"

/**
 * Desktop-style floating panel shared by the live-TV and chart widgets: fixed over the page, dragged by its title
 * bar, resized by the corner grip, minimised to a pill. The owner keeps the box (and persists it); this only turns
 * pointer moves into new positions and sizes, clamped so the bar can never leave the viewport. Pointer-downs on a
 * control inside the title bar (button, input, select, or anything marked data-nodrag) do not start a drag, so
 * headers hold their widgets without stopping propagation by hand.
 */
export function FloatingWindow({ x, y, w, h, min, minW = 280, minH = 160, pillW = 220, onMove, onResize, title, children, resizeTitle, className }: {
  x: number; y: number; w: number; h: number; min: boolean; minW?: number; minH?: number
  /** Width of the minimised pill (the title bar alone). */
  pillW?: number
  onMove: (p: { x: number; y: number }) => void
  /** The corner grip moved: both sizes, already clamped; an owner with a fixed aspect keeps only `w`. */
  onResize: (s: { w: number; h: number }) => void
  title: ReactNode
  children?: ReactNode
  resizeTitle?: string
  className?: string
}) {
  const drag = useRef<{ dx: number; dy: number } | null>(null)
  const size = useRef<{ x: number; y: number; w: number; h: number } | null>(null)
  // Latest callbacks behind stable refs: the window listeners are attached once, not on every render.
  const cb = useRef({ onMove, onResize, minW, minH })
  useEffect(() => { cb.current = { onMove, onResize, minW, minH } }, [onMove, onResize, minW, minH])
  useEffect(() => {
    const move = (e: PointerEvent) => {
      const { onMove, onResize, minW, minH } = cb.current
      if (drag.current) onMove({ x: Math.max(0, Math.min(window.innerWidth - 120, e.clientX - drag.current.dx)), y: Math.max(0, Math.min(window.innerHeight - 40, e.clientY - drag.current.dy)) })
      if (size.current) onResize({ w: Math.max(minW, Math.min(window.innerWidth - 32, size.current.w + (e.clientX - size.current.x))), h: Math.max(minH, Math.min(window.innerHeight - 32, size.current.h + (e.clientY - size.current.y))) })
    }
    const up = () => { drag.current = null; size.current = null }
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up)
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up) }
  }, [])
  return (
    <div className={cn("rise fixed z-40 select-none overflow-hidden rounded-xl border border-border bg-card/95 shadow-2xl shadow-black/40 backdrop-blur", className)} style={{ left: x, top: y, width: min ? pillW : w }}>
      <div
        className="flex cursor-grab items-center gap-2 border-b border-border/60 px-3 py-1.5 text-xs active:cursor-grabbing"
        onPointerDown={(e) => { if ((e.target as HTMLElement).closest("button, input, select, [data-nodrag]")) return; drag.current = { dx: e.clientX - x, dy: e.clientY - y } }}
      >
        {title}
      </div>
      {!min && (
        <div className="relative">
          {children}
          {/* visible resize grip: drag the corner to grow/shrink */}
          <div
            className="absolute bottom-0 right-0 z-10 flex size-7 cursor-nwse-resize items-end justify-end rounded-tl-md bg-gradient-to-tl from-card/90 to-transparent p-1 text-muted-foreground hover:text-foreground"
            onPointerDown={(e) => { e.stopPropagation(); e.preventDefault(); size.current = { x: e.clientX, y: e.clientY, w, h } }}
            title={resizeTitle}
          >
            <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden><path d="M11 1v10H1" fill="none" stroke="currentColor" strokeWidth="1.5" /><path d="M11 5v6H5M11 9v2H9" fill="none" stroke="currentColor" strokeWidth="1.5" /></svg>
          </div>
        </div>
      )}
    </div>
  )
}
