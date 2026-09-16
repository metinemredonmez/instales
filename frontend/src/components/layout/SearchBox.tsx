import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react"
import { useNavigate } from "react-router-dom"
import { useQuery } from "@tanstack/react-query"
import { Search } from "lucide-react"
import { api, type SearchHit } from "@/lib/api"
import { useMarket } from "@/lib/market"
import { cn } from "@/lib/utils"

const KIND_LABEL: Record<SearchHit["kind"], string> = { stock: "Hisse", fund: "Fon", institution: "Kurum" }

/** Header typeahead: queries /search (stocks, funds, institutions of the active market); Enter opens the highlighted hit. */
export function SearchBox({ className }: { className?: string }) {
  const navigate = useNavigate()
  const { market } = useMarket()
  const [q, setQ] = useState("")
  const [debounced, setDebounced] = useState("")
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  useEffect(() => {
    const t = setTimeout(() => setDebounced(q.trim()), 150)
    return () => clearTimeout(t)
  }, [q])

  const hits = useQuery({
    queryKey: ["search", market, debounced],
    queryFn: () => api.search(market, debounced),
    enabled: debounced.length > 0,
    staleTime: 60_000,
    placeholderData: (prev) => prev,
  })
  const rows = debounced ? hits.data ?? [] : []

  useEffect(() => { setActive(0) }, [rows.length, debounced])

  // close on outside click
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (!boxRef.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [])

  const go = (hit: SearchHit | undefined) => {
    const s = q.trim().toUpperCase()
    if (!hit && !s) return
    // No hit yet (or offline): fall back to the most likely route by shape.
    navigate(hit ? hit.href : market === "TR" && s.length === 3 ? `/funds/${s}` : `/stocks/${s}`)
    setQ("")
    setDebounced("")
    setOpen(false)
  }

  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, rows.length - 1)) }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    else if (e.key === "Enter") { e.preventDefault(); go(rows[active]) }
    else if (e.key === "Escape") { setOpen(false); (e.target as HTMLInputElement).blur() }
  }

  const showList = open && debounced.length > 0

  return (
    <div ref={boxRef} className={cn("relative", className)}>
      <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
      <input
        value={q}
        onChange={(e) => { setQ(e.target.value); setOpen(true) }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKey}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-autocomplete="list"
        placeholder={market === "TR" ? "Hisse, fon veya kurum: ASELS, TMV…" : "Ticker or filer: NVDA, Berkshire…"}
        className="h-9 w-full rounded-md border border-input bg-card pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring/40"
      />
      {showList && (
        <ul id={listId} role="listbox" className="absolute left-0 right-0 top-full z-40 mt-1 max-h-80 overflow-auto rounded-md border border-border bg-popover p-1 text-sm shadow-lg">
          {rows.length === 0 && (
            <li className="px-2.5 py-2 text-muted-foreground">
              {hits.isFetching ? "Aranıyor…" : <>Sonuç yok — <button type="button" className="underline" onMouseDown={() => go(undefined)}>“{q.trim().toUpperCase()}” sayfasını dene</button></>}
            </li>
          )}
          {rows.map((h, i) => (
            <li
              key={`${h.kind}:${h.key}`}
              role="option"
              aria-selected={i === active}
              onMouseEnter={() => setActive(i)}
              onMouseDown={(e) => { e.preventDefault(); go(h) }}
              className={cn("flex cursor-pointer items-center gap-2 rounded-[5px] px-2.5 py-1.5", i === active ? "bg-accent text-foreground" : "text-foreground")}
            >
              <span className="w-12 shrink-0 rounded-sm bg-muted px-1 py-px text-center font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{KIND_LABEL[h.kind]}</span>
              <span className="font-medium">{h.label}</span>
              <span className="truncate text-muted-foreground">{h.name}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
