import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { Minus, Tv, X } from "lucide-react"
import { api } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { cn } from "@/lib/utils"

const POS_KEY = "instilens.tv"
type Saved = { x: number; y: number; w: number; channel: string; min: boolean }
const load = (): Saved | null => { try { return JSON.parse(localStorage.getItem(POS_KEY) || "null") } catch { return null } }
const save = (s: Saved) => { try { localStorage.setItem(POS_KEY, JSON.stringify(s)) } catch { /* ignore */ } }

/**
 * Floating live-TV window (YouTube live embed), like a desktop widget: draggable by its title bar, resizable by the
 * corner, minimises to a pill, remembers position/channel. Muted autoplay (browsers block sound until a click).
 */
export function LiveTvWidget({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t } = useI18n()
  const q = useQuery({ queryKey: ["live-tv"], queryFn: api.liveTv, staleTime: 600_000, enabled: open })
  const saved = load()
  const [pos, setPos] = useState({ x: saved?.x ?? Math.max(16, window.innerWidth - 480), y: saved?.y ?? Math.max(80, window.innerHeight - 340) })
  const [w, setW] = useState(saved?.w ?? 440)
  const [channel, setChannel] = useState(saved?.channel ?? "")
  const [min, setMin] = useState(saved?.min ?? false)
  const drag = useRef<{ dx: number; dy: number } | null>(null)
  const size = useRef<{ x: number; w: number } | null>(null)
  const list = q.data ?? []
  const cur = list.find((c) => c.channel_id === channel) ?? list[0]

  useEffect(() => { save({ x: pos.x, y: pos.y, w, channel: cur?.channel_id ?? "", min }) }, [pos, w, cur?.channel_id, min])
  useEffect(() => {
    const move = (e: PointerEvent) => {
      if (drag.current) setPos({ x: Math.max(0, Math.min(window.innerWidth - 120, e.clientX - drag.current.dx)), y: Math.max(0, Math.min(window.innerHeight - 40, e.clientY - drag.current.dy)) })
      if (size.current) setW(Math.max(280, Math.min(960, size.current.w + (e.clientX - size.current.x))))
    }
    const up = () => { drag.current = null; size.current = null }
    window.addEventListener("pointermove", move); window.addEventListener("pointerup", up)
    return () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up) }
  }, [])
  if (!open) return null
  const h = Math.round(w * 9 / 16)
  return (
    <div className={cn("rise fixed z-40 select-none overflow-hidden rounded-xl border border-border bg-card/95 shadow-2xl shadow-black/40 backdrop-blur")} style={{ left: pos.x, top: pos.y, width: min ? 220 : w }}>
      <div className="flex cursor-grab items-center gap-2 border-b border-border/60 px-3 py-1.5 text-xs active:cursor-grabbing" onPointerDown={(e) => { drag.current = { dx: e.clientX - pos.x, dy: e.clientY - pos.y } }}>
        <span className="live-dot size-1.5 rounded-full bg-negative text-negative" />
        <Tv className="size-3.5 text-primary" />
        <span className="font-semibold">{t("tv.title")}</span>
        {!min && list.length > 0 && (
          <select value={cur?.channel_id ?? ""} onChange={(e) => setChannel(e.target.value)} onPointerDown={(e) => e.stopPropagation()} className="ml-1 h-6 max-w-[160px] rounded border border-border bg-background px-1 text-[11px] outline-none">
            {list.map((c) => <option key={c.channel_id} value={c.channel_id}>{c.name}</option>)}
          </select>
        )}
        {min && cur && <span className="truncate text-muted-foreground">{cur.name}</span>}
        <span className="ml-auto" />
        <button onClick={() => setMin(!min)} onPointerDown={(e) => e.stopPropagation()} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label={t("tv.hint")}><Minus className="size-3.5" /></button>
        <button onClick={onClose} onPointerDown={(e) => e.stopPropagation()} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label={t("common.close")}><X className="size-3.5" /></button>
      </div>
      {!min && (
        <div className="relative bg-black" style={{ height: h }}>
          {cur ? (
            <iframe key={cur.channel_id} src={cur.embed} title={cur.name} className="absolute inset-0 size-full" allow="autoplay; encrypted-media; picture-in-picture; fullscreen" allowFullScreen referrerPolicy="strict-origin-when-cross-origin" />
          ) : (
            <div className="grid size-full place-items-center px-6 text-center text-xs text-muted-foreground">{q.isLoading ? "…" : t("tv.unavailable")}</div>
          )}
          <div className="absolute bottom-0 right-0 size-4 cursor-nwse-resize" onPointerDown={(e) => { e.stopPropagation(); size.current = { x: e.clientX, w } }} title="↔" />
        </div>
      )}
    </div>
  )
}
