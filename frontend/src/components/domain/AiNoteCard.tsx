import { useQuery, useQueryClient } from "@tanstack/react-query"
import { ChevronDown, Eye, RefreshCw, Sparkles, Zap } from "lucide-react"
import { useState } from "react"
import { api, type AiNote, type Market } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { fmtDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import { SpeakButton } from "./SpeakButton"
import { RichNote } from "./RichNote"

/** Descriptive AI note (flows × headlines). Never advice; shows model + time + what it was written from. */
export function AiNoteCard({ market, symbol, title }: { market: Market; symbol?: string; title: string }) {
  const { user } = useAuth()
  const { lang, t } = useI18n()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  // Collapsed state is remembered per kind (brief vs stock note) so the card stays out of the way once closed.
  const storeKey = `instilens.ainote.${symbol ? "stock" : "brief"}`
  const [open, setOpen] = useState(() => { try { return localStorage.getItem(storeKey) !== "closed" } catch { return true } })
  const toggleOpen = () => { setOpen((o) => { try { localStorage.setItem(storeKey, o ? "closed" : "open") } catch { /* ignore */ } return !o }) }
  const key = ["ai-note", market, symbol ?? "market", lang]
  const q = useQuery({ queryKey: key, queryFn: () => (symbol ? api.stockAi(market, symbol, lang) : api.brief(market, lang)), retry: false, staleTime: 5 * 60_000 })
  const refresh = async () => { setBusy(true); try { const n = symbol ? await api.stockAi(market, symbol, lang, true) : await api.brief(market, lang, true); qc.setQueryData(key, n) } finally { setBusy(false) } }
  const note = q.data as AiNote | null | undefined
  if (q.isLoading) return <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">{t("ai.preparing")}</div>
  if (q.isError) return <div className="rounded-lg border border-dashed border-border px-4 py-2.5 text-xs text-muted-foreground">{t("ai.unavailable")}{user?.role === "ADMIN" ? ` — ${(q.error as Error).message}` : ""}</div>
  if (!note) return null
  return (
    <div className={cn("rounded-lg border border-primary/30 bg-gradient-to-br from-primary/10 via-card to-card", open ? "p-4" : "px-4 py-2.5")}>
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <button onClick={toggleOpen} aria-expanded={open} className="inline-flex items-center gap-2 hover:text-foreground">
          <Sparkles className="size-3.5 text-primary" /> <span className="font-semibold text-foreground">{title}</span>
          <ChevronDown className={cn("size-3.5 transition", !open && "-rotate-90")} />
        </button>
        <span>· {fmtDateTime(note.created_at)}</span><span className="hidden sm:inline">· {note.model}</span>
        {!open && <span className="hidden min-w-0 flex-1 truncate md:inline">— {note.headline || note.content}</span>}
        <span className="ml-auto" />
        {open && <SpeakButton noteId={note.id} text={`${note.content} ${note.watch.map((w) => `${t("ai.watch")}: ${w}.`).join(" ")}`} lang={note.lang} />}
        {open && user?.role === "ADMIN" && <button onClick={refresh} disabled={busy} className="inline-flex items-center gap-1 hover:text-foreground"><RefreshCw className={cn("size-3", busy && "animate-spin")} /> {t("common.refresh")}</button>}
        {!open && <button onClick={toggleOpen} className="shrink-0 rounded-md border border-border bg-card px-2 py-1 text-xs hover:bg-accent">{t("common.show")}</button>}
      </div>
      {open && note.headline && <div className="mt-3 text-lg font-semibold leading-snug tracking-tight">{note.headline}</div>}
      {open && note.highlights?.length > 0 && (
        <ul className="rise-stagger mt-3 grid gap-2 sm:grid-cols-3">
          {note.highlights.map((h) => (
            <li key={h} className="flex gap-2 rounded-md border border-border bg-card px-3 py-2 text-sm leading-snug"><Zap className="mt-0.5 size-3.5 shrink-0 text-primary" /><span>{h}</span></li>
          ))}
        </ul>
      )}
      {open && <RichNote note={note} className="mt-3" />}
      {open && note.watch.length > 0 && (
        <ul className="mt-3 flex flex-wrap gap-1.5">{note.watch.map((w) => <li key={w} className="inline-flex items-center gap-1.5 rounded-sm border border-border bg-card px-2 py-1 text-xs"><Eye className="size-3 text-muted-foreground" /> {w}</li>)}</ul>
      )}
      {open && note.confidence_note && <div className="mt-2 text-xs text-muted-foreground">{note.confidence_note}</div>}
      {open && <div className="mt-2 text-[10px] uppercase tracking-wider text-muted-foreground">{t("ai.disclaimer")}</div>}
    </div>
  )
}
