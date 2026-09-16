import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react"
import { useNavigate } from "react-router-dom"
import { useQueryClient } from "@tanstack/react-query"
import { Dialog } from "radix-ui"
import { Globe, Languages, Search, SunMoon, Volume2 } from "lucide-react"
import { api, type AiNote, type Market, type SearchHit } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n, type Lang } from "@/lib/i18n"
import type { Key } from "@/i18n/tr"
import { useMarket } from "@/lib/market"
import { useTheme } from "@/lib/theme"
import { tts } from "@/lib/tts"
import { cn } from "@/lib/utils"
import { noteSpeechText } from "@/components/domain/AiNoteCard"
import { ADMIN, MINE, NAV } from "./nav"
import { usePickLang } from "./prefs"
import { useSearch } from "./useSearch"

type Command = { id: string; label: string; hint?: string; icon: React.ComponentType<{ className?: string }>; run: () => void; section: "pages" | "actions" }
type Row = { id: string; hit?: SearchHit; cmd?: Command }

const MARKET_NAME: Record<Market, Key> = { TR: "market.name.TR", US: "market.name.US" }
const LANG_NAME: Record<Lang, Key> = { tr: "menu.lang.tr", en: "menu.lang.en" }

/**
 * ⌘K / Ctrl+K: one box for the header search (same hook as SearchBox) and the app's commands — pages, market,
 * theme, language, "listen to the brief". Arrow keys move (scrolling the active row into view), Enter runs the
 * highlighted row only — a query with no match is not guessed into a route here, the empty state offers that as an
 * explicit button — and Escape closes; Radix supplies the focus trap.
 */
export function CommandPalette() {
  const { t, lang } = useI18n()
  const { user } = useAuth()
  const { market, setMarket } = useMarket()
  const { toggle } = useTheme()
  const pickLang = usePickLang()
  const navigate = useNavigate()
  const qc = useQueryClient()
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  // Set by the key handler so only keyboard moves scroll the list; hovering a half-visible row must not jump it.
  const scrollActive = useRef(false)
  const listId = useId()
  const search = useSearch()
  const KIND_LABEL: Record<SearchHit["kind"], string> = { stock: t("common.stock"), fund: t("common.fund"), institution: t("inst.institution") }

  useEffect(() => {
    const onKey = (e: globalThis.KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && !e.altKey && e.key.toLowerCase() === "k") { e.preventDefault(); setOpen((o) => !o) }
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [])

  const close = () => setOpen(false)
  // Whatever closed it (Escape, ⌘K again, a command), the next open starts empty.
  const { reset } = search
  useEffect(() => { if (!open) reset() }, [open, reset])

  /** Read today's brief aloud when the Radar page has already loaded it; otherwise go there so it loads. */
  const listenBrief = async () => {
    const note = qc.getQueryData<AiNote | null>(["ai-note", market, "market", lang])
    if (!note) { navigate("/"); return }
    const status = await qc.fetchQuery({ queryKey: ["tts-status"], queryFn: api.ttsStatus, staleTime: 600_000 }).catch(() => ({ provider: null }))
    tts.play({ noteId: note.id, title: note.headline || t("radar.brief"), text: noteSpeechText(note, t), lang: note.lang, provider: status.provider, errorMessage: (p) => t("tts.serverError", { p }) })
  }

  const commands = useMemo<Command[]>(() => {
    const pages: Command[] = [...NAV, ...MINE, ...(user?.role === "ADMIN" ? [ADMIN] : [])].map((n) => ({ id: `page:${n.to}`, label: t(n.key), icon: n.icon, section: "pages", run: () => navigate(n.to) }))
    const other = market === "TR" ? "US" : "TR"
    const otherLang: Lang = lang === "tr" ? "en" : "tr"
    const actions: Command[] = [
      { id: "market", label: t("palette.market", { m: `${t(MARKET_NAME[other])} (${other})` }), icon: Globe, section: "actions", run: () => setMarket(other) },
      { id: "theme", label: t("palette.theme"), icon: SunMoon, section: "actions", run: toggle },
      { id: "lang", label: t("palette.lang", { l: t(LANG_NAME[otherLang]) }), icon: Languages, section: "actions", run: () => pickLang(otherLang) },
      { id: "brief", label: t("palette.listenBrief"), hint: t("palette.listenBriefHint"), icon: Volume2, section: "actions", run: () => { listenBrief() } },
    ]
    return [...pages, ...actions]
  }, [t, user?.role, market, lang, navigate, setMarket, toggle, pickLang]) // eslint-disable-line react-hooks/exhaustive-deps

  const needle = search.q.trim().toLocaleLowerCase(lang === "tr" ? "tr-TR" : "en")
  const matched = needle ? commands.filter((c) => c.label.toLocaleLowerCase(lang === "tr" ? "tr-TR" : "en").includes(needle)) : commands
  const rows: Row[] = [...search.rows.map((h) => ({ id: `${h.kind}:${h.key}`, hit: h })), ...matched.map((c) => ({ id: c.id, cmd: c }))]
  // Listbox children must be options or groups: one group per section (results / pages / actions), rows numbered globally.
  const sections: { label: string; rows: { row: Row; i: number }[] }[] = []
  rows.forEach((row, i) => {
    const label = row.hit ? t("palette.results") : row.cmd!.section === "pages" ? t("palette.pages") : t("palette.actions")
    const last = sections[sections.length - 1]
    if (last && last.label === label) last.rows.push({ row, i })
    else sections.push({ label, rows: [{ row, i }] })
  })

  useEffect(() => { setActive(0); document.getElementById(listId)?.scrollTo?.(0, 0) }, [rows.length, search.debounced, listId])
  useEffect(() => {
    if (!scrollActive.current) return
    scrollActive.current = false
    document.getElementById(`${listId}-${active}`)?.scrollIntoView?.({ block: "nearest" })
  }, [active, listId])

  const run = (row: Row) => {
    if (row.hit) { search.go(row.hit); setOpen(false); return }
    row.cmd?.run()
    close()
  }
  /** The empty state's "try the X page": the search box's shape-based route, only on an explicit click. */
  const tryPage = () => { if (search.go(undefined)) setOpen(false) }

  const move = (to: (a: number) => number) => { scrollActive.current = true; setActive(to) }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); move((a) => Math.min(a + 1, rows.length - 1)) }
    else if (e.key === "ArrowUp") { e.preventDefault(); move((a) => Math.max(a - 1, 0)) }
    else if (e.key === "Home") { e.preventDefault(); move(() => 0) }
    else if (e.key === "End") { e.preventDefault(); move(() => rows.length - 1) }
    else if (e.key === "Enter") { e.preventDefault(); const row = rows[active]; if (row) run(row) }
  }

  return (
    <Dialog.Root open={open} onOpenChange={(o) => (o ? setOpen(true) : close())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-background/60 backdrop-blur-[2px] data-[state=open]:animate-in data-[state=open]:fade-in-0" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed left-1/2 top-[12vh] z-50 w-[min(560px,calc(100vw-2rem))] -translate-x-1/2 overflow-hidden rounded-lg border border-border bg-popover shadow-2xl outline-none data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95"
        >
          <Dialog.Title className="sr-only">{t("palette.open")}</Dialog.Title>
          <div className="relative border-b border-border/70">
            <Search className="pointer-events-none absolute left-3 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              autoFocus
              value={search.q}
              onChange={(e) => search.setQ(e.target.value)}
              onKeyDown={onKey}
              role="combobox"
              aria-expanded={rows.length > 0}
              aria-controls={rows.length > 0 ? listId : undefined}
              aria-autocomplete="list"
              aria-activedescendant={rows[active] ? `${listId}-${active}` : undefined}
              placeholder={t("palette.placeholder")}
              className="h-12 w-full bg-transparent pl-10 pr-3 text-sm outline-none placeholder:text-muted-foreground"
            />
          </div>
          {rows.length === 0 && (
            <div className="px-3.5 py-3 text-sm text-muted-foreground" aria-live="polite">
              {search.fetching ? t("search.searching") : needle ? <>{t("palette.empty")} — <button type="button" className="underline" onMouseDown={(e) => { e.preventDefault(); tryPage() }}>{t("search.tryPage", { q: search.q.trim().toUpperCase() })}</button></> : t("palette.empty")}
            </div>
          )}
          <ul id={listId} role="listbox" aria-label={t("palette.open")} className={cn("max-h-[min(60vh,420px)] overflow-y-auto p-1", rows.length === 0 && "hidden")}>
            {sections.map((sec) => (
              <li key={sec.label} role="group" aria-label={sec.label}>
                <div aria-hidden className="px-2.5 pb-1 pt-2 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/70">{sec.label}</div>
                <ul role="presentation">
                  {sec.rows.map(({ row, i }) => {
                    const Icon = row.cmd?.icon
                    return (
                      <li
                        key={row.id}
                        id={`${listId}-${i}`}
                        role="option"
                        aria-selected={i === active}
                        onMouseEnter={() => setActive(i)}
                        onMouseDown={(e) => { e.preventDefault(); run(row) }}
                        className={cn("flex cursor-pointer items-center gap-2.5 rounded-[5px] px-2.5 py-2 text-sm", i === active ? "bg-accent text-foreground" : "text-foreground")}
                      >
                        {row.hit ? (
                          <>
                            <span className="w-12 shrink-0 rounded-sm bg-muted px-1 py-px text-center font-mono text-[10px] uppercase tracking-wider text-muted-foreground">{KIND_LABEL[row.hit.kind]}</span>
                            <span className="font-medium">{row.hit.label}</span>
                            <span className="truncate text-muted-foreground">{row.hit.name}</span>
                          </>
                        ) : (
                          <>
                            {Icon && <Icon className="size-4 shrink-0 text-muted-foreground" />}
                            <span>{row.cmd!.label}</span>
                            {row.cmd!.hint && <span className="truncate text-xs text-muted-foreground">— {row.cmd!.hint}</span>}
                          </>
                        )}
                      </li>
                    )
                  })}
                </ul>
              </li>
            ))}
          </ul>
          <div className="flex items-center justify-between border-t border-border/70 px-3 py-1.5 text-[10px] text-muted-foreground">
            <span>{t("palette.hint")}</span>
            <span className="flex gap-1"><kbd className="rounded-sm border border-border bg-muted px-1 font-mono">Esc</kbd> {t("common.close")}</span>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  )
}
