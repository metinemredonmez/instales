import { Loader2, Pause, Play, Volume2, X } from "lucide-react"
import { useEffect } from "react"
import { useI18n } from "@/lib/i18n"
import { tts, useTts } from "@/lib/tts"
import { cn } from "@/lib/utils"

/**
 * Small fixed card while narration is preparing / playing / paused. Reads the same store as SpeakButton — nothing is
 * decided here. Sits above the phone bottom bar; hides when playback ends. Unmounting (logout) stops the audio.
 */
export function NowSpeaking() {
  const { t } = useI18n()
  const s = useTts()
  useEffect(() => () => tts.stop(), [])
  if (s.status === "idle") return null
  const preparing = s.status === "preparing"
  const paused = s.status === "paused"
  return (
    <div
      role="status"
      aria-live="polite"
      className="rise fixed bottom-16 right-4 z-40 w-[calc(100vw-2rem)] overflow-hidden rounded-lg border border-border bg-card/95 shadow-md backdrop-blur sm:w-80 md:bottom-4"
    >
      <div className="flex items-center gap-2.5 px-3 py-2">
        <span className="inline-flex size-7 shrink-0 items-center justify-center rounded-md bg-primary/10 text-primary">
          {preparing ? <Loader2 className="size-3.5 animate-spin" /> : <Volume2 className={cn("size-3.5", s.status === "playing" && "motion-safe:animate-pulse")} />}
        </span>
        <div className="min-w-0 flex-1 leading-tight">
          <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{preparing ? t("tts.preparing") : t("tts.now")}</div>
          <div className="truncate text-xs font-medium" title={s.title}>{s.title}</div>
          <div className="truncate text-[10px] text-muted-foreground">{s.gender === "male" ? t("tts.male") : t("tts.female")} · {s.provider ?? t("tts.browser")}</div>
        </div>
        {!preparing && (
          <button onClick={tts.toggle} className="inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-border hover:bg-accent" title={paused ? t("tts.resume") : t("tts.pause")} aria-label={paused ? t("tts.resume") : t("tts.pause")}>
            {paused ? <Play className="size-3.5" /> : <Pause className="size-3.5" />}
          </button>
        )}
        <button onClick={tts.stop} className="inline-flex size-7 shrink-0 items-center justify-center rounded-md text-muted-foreground hover:bg-accent hover:text-foreground" title={t("tts.stop")} aria-label={t("tts.stop")}>
          <X className="size-3.5" />
        </button>
      </div>
      {/* Progress only when we actually know it (server audio duration, or spoken characters for the browser voice). */}
      {s.progress !== null && (
        <div className="h-0.5 w-full bg-border/70">
          <div className="bar-anim h-full bg-primary" style={{ width: `${Math.round(s.progress * 100)}%` }} />
        </div>
      )}
    </div>
  )
}
