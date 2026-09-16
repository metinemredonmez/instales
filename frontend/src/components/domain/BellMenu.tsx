import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query"
import { Bell } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { Link } from "react-router-dom"
import { api } from "@/lib/api"
import { fmtDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"

/** In-app notification center: unread badge, last 10, click-through, mark all read. */
export function BellMenu() {
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const notes = useQuery({ queryKey: ["notifications"], queryFn: api.notifications, refetchInterval: 30_000 })
  const markAll = useMutation({ mutationFn: () => api.markRead(), onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }) })
  const markOne = useMutation({ mutationFn: (id: number) => api.markRead(id), onSuccess: () => qc.invalidateQueries({ queryKey: ["notifications"] }) })
  const unread = notes.data?.filter((n) => !n.read_at).length ?? 0
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [])
  return (
    <div ref={ref} className="relative">
      <button onClick={() => setOpen(!open)} aria-label="Bildirimler" className={cn("relative grid size-9 place-items-center rounded-md hover:bg-accent", open && "bg-accent")}>
        <Bell className="size-4" />
        {unread > 0 && <span className="absolute right-1 top-1 grid min-w-4 place-items-center rounded-full bg-primary px-1 text-[9px] font-bold text-primary-foreground">{unread}</span>}
      </button>
      {open && (
        <div className="absolute right-0 top-full z-50 mt-1 w-[min(380px,calc(100vw-2rem))] rounded-lg border border-border bg-popover shadow-xl">
          <div className="flex items-center justify-between border-b border-border/70 px-3 py-2 text-xs">
            <span className="font-semibold">Bildirimler {unread > 0 && <span className="text-muted-foreground">· {unread} okunmamış</span>}</span>
            <div className="flex gap-2">
              {unread > 0 && <button onClick={() => markAll.mutate()} className="text-muted-foreground hover:text-foreground">tümünü okundu</button>}
              <Link to="/alerts" onClick={() => setOpen(false)} className="text-primary hover:underline">Alarmlar →</Link>
            </div>
          </div>
          <ul className="max-h-[420px] divide-y divide-border/60 overflow-y-auto">
            {notes.data?.slice(0, 10).map((n) => (
              <li key={n.id} className={cn("px-3 py-2", !n.read_at && "bg-primary/5")}>
                <Link to={n.link ?? "/alerts"} onClick={() => { markOne.mutate(n.id); setOpen(false) }} className="block">
                  <div className="flex items-start gap-2">
                    {!n.read_at && <span className="mt-1.5 size-1.5 shrink-0 rounded-full bg-primary" />}
                    <div className="min-w-0">
                      <div className="truncate text-sm font-medium">{n.title}</div>
                      <div className="truncate text-xs text-muted-foreground">{n.body}</div>
                      <div className="num mt-0.5 text-[10px] text-muted-foreground">{fmtDateTime(n.created_at)}</div>
                    </div>
                  </div>
                </Link>
              </li>
            ))}
            {notes.data?.length === 0 && <li className="px-3 py-6 text-center text-xs text-muted-foreground">Henüz bildirim yok. Takip listene hisse/fon ekle; olaylar buraya düşer.</li>}
          </ul>
        </div>
      )}
    </div>
  )
}
