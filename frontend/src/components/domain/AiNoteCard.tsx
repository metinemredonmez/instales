import { useQuery, useQueryClient } from "@tanstack/react-query"
import { RefreshCw, Sparkles } from "lucide-react"
import { useState } from "react"
import { api, type AiNote, type Market } from "@/lib/api"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { fmtDateTime } from "@/lib/format"
import { cn } from "@/lib/utils"
import { SpeakButton } from "./SpeakButton"

/** Descriptive AI note (flows × headlines). Never advice; shows model + time + what it was written from. */
export function AiNoteCard({ market, symbol, title }: { market: Market; symbol?: string; title: string }) {
  const { user } = useAuth()
  const { lang, t } = useI18n()
  const qc = useQueryClient()
  const [busy, setBusy] = useState(false)
  const key = ["ai-note", market, symbol ?? "market", lang]
  const q = useQuery({ queryKey: key, queryFn: () => (symbol ? api.stockAi(market, symbol, lang) : api.brief(market, lang)), retry: false, staleTime: 5 * 60_000 })
  const refresh = async () => { setBusy(true); try { const n = symbol ? await api.stockAi(market, symbol, lang, true) : await api.brief(market, lang, true); qc.setQueryData(key, n) } finally { setBusy(false) } }
  const note = q.data as AiNote | null | undefined
  if (q.isLoading) return <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground">{t("ai.preparing")}</div>
  if (!note) return null
  return (
    <div className="rounded-lg border border-primary/30 bg-primary/5 p-4">
      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <Sparkles className="size-3.5 text-primary" /> <span className="font-semibold text-foreground">{title}</span>
        <span>· {fmtDateTime(note.created_at)}</span><span className="hidden sm:inline">· {note.model}</span>
        <span className="ml-auto" /><SpeakButton noteId={note.id} text={`${note.content} ${note.watch.map((w) => `${t("ai.watch")}: ${w}.`).join(" ")}`} lang={note.lang} />
        {user?.role === "ADMIN" && <button onClick={refresh} disabled={busy} className="inline-flex items-center gap-1 hover:text-foreground"><RefreshCw className={cn("size-3", busy && "animate-spin")} /> {t("common.refresh")}</button>}
      </div>
      <p className="mt-2 text-sm leading-relaxed">{note.content}</p>
      {note.watch.length > 0 && (
        <ul className="mt-2 flex flex-wrap gap-1.5">{note.watch.map((w) => <li key={w} className="rounded-sm border border-border bg-card px-2 py-0.5 text-xs">👁 {w}</li>)}</ul>
      )}
      {note.confidence_note && <div className="mt-2 text-xs text-muted-foreground">{note.confidence_note}</div>}
      <div className="mt-2 text-[10px] uppercase tracking-wider text-muted-foreground">{t("ai.disclaimer")}</div>
    </div>
  )
}
