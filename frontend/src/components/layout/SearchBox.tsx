import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react"
import { Search } from "lucide-react"
import type { SearchHit } from "@/lib/api"
import { cn } from "@/lib/utils"
import { useI18n } from "@/lib/i18n"
import { useSearch } from "./useSearch"

/** Header typeahead: queries /search (stocks, funds, institutions of the active market); Enter opens the highlighted hit. */
export function SearchBox({ className, shortcutHint }: { className?: string; shortcutHint?: string }) {
  const { t } = useI18n()
  const KIND_LABEL: Record<SearchHit["kind"], string> = { stock: t("common.stock"), fund: t("common.fund"), institution: t("inst.institution") }
  const { q, setQ, debounced, rows, fetching, go: open_, market } = useSearch()
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const boxRef = useRef<HTMLDivElement>(null)
  const listId = useId()

  useEffect(() => { setActive(0) }, [rows.length, debounced])

  // close on outside click
  useEffect(() => {
    const onDoc = (e: MouseEvent) => { if (!boxRef.current?.contains(e.target as Node)) setOpen(false) }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [])

  const go = (hit: SearchHit | undefined) => { if (open_(hit)) setOpen(false) }

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
        placeholder={market === "TR" ? t("search.ph.tr") : t("search.ph.us")}
        className={cn("h-9 w-full rounded-md border border-input bg-card pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring/40", shortcutHint && "md:pr-12")}
      />
      {shortcutHint && !q && (
        <kbd aria-hidden className="pointer-events-none absolute right-2 top-1/2 hidden -translate-y-1/2 rounded-sm border border-border bg-muted px-1.5 py-px font-mono text-[10px] text-muted-foreground md:inline">{shortcutHint}</kbd>
      )}
      {showList && (
        <ul id={listId} role="listbox" className="absolute left-0 right-0 top-full z-40 mt-1 max-h-80 overflow-auto rounded-md border border-border bg-popover p-1 text-sm shadow-lg">
          {rows.length === 0 && (
            <li className="px-2.5 py-2 text-muted-foreground">
              {fetching ? t("search.searching") : <>{t("search.noResults")} — <button type="button" className="underline" onMouseDown={() => go(undefined)}>{t("search.tryPage", { q: q.trim().toUpperCase() })}</button></>}
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
