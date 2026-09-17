import { useQuery } from "@tanstack/react-query"
import { Loader2, Pause, Volume2 } from "lucide-react"
import { api } from "@/lib/api"
import { hasFeature, useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { tts, useTts } from "@/lib/tts"
import { PlanLockedInline, planName } from "./PlanGate"
import { VoicePicker } from "./VoicePicker"

/**
 * Read an AI note aloud. Server TTS (ElevenLabs/OpenAI) when configured — and then we do NOT silently fall back
 * to the browser voice on failure, we show the error so a bad key is visible. Browser voice only when no provider.
 * Playback state lives in lib/tts (shared with the NowSpeaking widget), so the note keeps playing across pages.
 * A plan without `tts` (while plans are enforced) gets the locked control instead: the audio route would answer
 * 402 on the <audio> element, which reads as a media error and would blame the server key.
 */
export function SpeakButton({ noteId, title, text, lang }: { noteId: number; title: string; text: string; lang: "tr" | "en" }) {
  const { t } = useI18n()
  const { user } = useAuth()
  const locked = !!user && !hasFeature(user, "tts")
  const status = useQuery({ queryKey: ["tts-status"], queryFn: api.ttsStatus, staleTime: 600_000, enabled: !locked })
  const s = useTts()
  const mine = s.noteId === noteId
  const loading = mine && s.status === "preparing"
  const playing = mine && (s.status === "playing" || s.status === "paused")
  const err = mine ? s.error : null
  const provider = status.data?.provider ?? null

  const play = () => {
    if (loading || playing) return tts.stop()
    tts.play({ noteId, title, text, lang, provider, errorMessage: (p) => t("tts.serverError", { p }) })
  }
  if (locked) return <PlanLockedInline text={t("plan.locked.tts", { p: planName(t, "PRO") })} />
  return (
    <span className="inline-flex items-center gap-1">
      <VoicePicker lang={lang} compact />
      <button onClick={play} className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-xs hover:bg-accent" title={provider ? `${t("tts.title")} (${provider})` : `${t("tts.title")} (${t("tts.browser")})`}>
        {loading ? <Loader2 className="size-3.5 animate-spin" /> : playing ? <Pause className="size-3.5" /> : <Volume2 className="size-3.5" />} {loading ? t("tts.preparing") : playing ? t("tts.stop") : t("tts.listen")}
      </button>
      {!provider && status.data && <span className="text-[10px] text-warning" title={t("tts.noProvider")}>{t("tts.browser")}</span>}
      {err && <span className="text-[10px] text-negative">{err}</span>}
    </span>
  )
}
