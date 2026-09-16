import { useQuery } from "@tanstack/react-query"
import { useEffect, useRef, useState } from "react"
import { Check, ChevronDown, Mic2, Play } from "lucide-react"
import { api } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { tts, useTts } from "@/lib/tts"
import { cn } from "@/lib/utils"

const RATES = [0.8, 0.9, 1, 1.1, 1.25]

/**
 * One small pill ("🎙 Rachel · 1×") that opens a minimal menu: voices of the note's language with a ▶ audition
 * button each, and a speed strip. Picking a voice applies instantly — even mid-playback.
 */
export function VoicePicker({ lang, compact }: { lang: "tr" | "en"; compact?: boolean }) {
  const { t } = useI18n()
  const s = useTts()
  const q = useQuery({ queryKey: ["tts-voices", lang], queryFn: () => api.ttsVoices(lang), staleTime: 600_000 })
  const voices = q.data?.voices ?? []
  const [open, setOpen] = useState(false)
  const [auditioning, setAuditioning] = useState<string | null>(null)
  const ref = useRef<HTMLSpanElement>(null)
  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => { if (!ref.current?.contains(e.target as Node)) { setOpen(false); tts.stopPreview(); setAuditioning(null) } }
    document.addEventListener("mousedown", onDoc)
    return () => document.removeEventListener("mousedown", onDoc)
  }, [open])
  const currentId = s.voice[lang] ?? voices.find((v) => v.gender === s.gender && v.source === "config")?.id ?? ""
  const current = voices.find((v) => v.id === currentId)
  const label = current ? current.name.replace(/ \((varsayılan|default)\)$/, "") : s.gender === "male" ? t("tts.male") : t("tts.female")

  const audition = async (id: string) => {
    if (auditioning === id) { tts.stopPreview(); setAuditioning(null); return }
    setAuditioning(id)
    await tts.previewVoice(lang, id)
    setAuditioning((cur) => (cur === id ? null : cur))
  }

  return (
    <span ref={ref} className="relative inline-flex">
      <button onClick={() => setOpen(!open)} className={cn("inline-flex h-7 items-center gap-1.5 rounded-md border border-border bg-card px-2 text-[11px] hover:bg-accent", compact && "max-w-[170px]")} title={t("tts.voice")} aria-haspopup="menu" aria-expanded={open}>
        <Mic2 className="size-3 text-primary" /><span className="truncate">{label}</span><span className="text-muted-foreground">· {s.rate}×</span><ChevronDown className={cn("size-3 text-muted-foreground transition", open && "rotate-180")} />
      </button>
      {open && (
        <div role="menu" className="rise absolute right-0 top-full z-50 mt-1 w-64 rounded-lg border border-border bg-popover p-1.5 text-xs shadow-xl">
          <div className="px-2 pb-1 pt-0.5 text-[10px] uppercase tracking-wider text-muted-foreground">{t("tts.voice")} · {lang.toUpperCase()}</div>
          {voices.length === 0 && (
            <div className="flex gap-1 px-1 pb-1">
              {(["female", "male"] as const).map((g) => <button key={g} onClick={() => tts.setGender(g)} className={cn("flex-1 rounded-md px-2 py-1.5", s.gender === g ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50")}>{g === "male" ? t("tts.male") : t("tts.female")}</button>)}
            </div>
          )}
          <ul className="max-h-56 overflow-y-auto">
            {voices.map((v) => {
              const sel = v.id === currentId
              return (
                <li key={v.id} className={cn("flex items-center gap-2 rounded-md px-2 py-1.5", sel ? "bg-accent text-foreground" : "hover:bg-accent/50")}>
                  <button onClick={() => { tts.setVoice(lang, v.source === "config" ? null : v.id, v.gender) }} className="flex min-w-0 flex-1 items-center gap-2 text-left">
                    <span className={cn("size-1.5 shrink-0 rounded-full", v.gender === "male" ? "bg-primary" : "bg-warning")} />
                    <span className="truncate">{v.name}</span>
                    {sel && <Check className="ml-auto size-3.5 shrink-0 text-primary" />}
                  </button>
                  <button onClick={() => audition(v.id)} className={cn("shrink-0 rounded p-1 text-muted-foreground hover:bg-background hover:text-foreground", auditioning === v.id && "text-primary")} title={t("tts.audition")} aria-label={t("tts.audition")}>
                    <Play className={cn("size-3", auditioning === v.id && "animate-pulse")} />
                  </button>
                </li>
              )
            })}
          </ul>
          <div className="mt-1 border-t border-border/60 px-1 pt-1.5">
            <div className="mb-1 px-1 text-[10px] uppercase tracking-wider text-muted-foreground">{t("tts.speed")}</div>
            <div className="flex overflow-hidden rounded-md border border-border">
              {RATES.map((r) => <button key={r} onClick={() => tts.setRate(r)} className={cn("flex-1 py-1 text-[11px]", s.rate === r ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>{r}×</button>)}
            </div>
          </div>
        </div>
      )}
    </span>
  )
}
