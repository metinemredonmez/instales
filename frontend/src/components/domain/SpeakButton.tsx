import { useQuery } from "@tanstack/react-query"
import { Loader2, Pause, Volume2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { api } from "@/lib/api"
import { useI18n } from "@/lib/i18n"

/**
 * Read an AI note aloud. Server TTS (ElevenLabs/OpenAI) when configured — and then we do NOT silently fall back
 * to the browser voice on failure, we show the error so a bad key is visible. Browser voice only when no provider.
 */
export function SpeakButton({ noteId, text, lang }: { noteId: number; text: string; lang: "tr" | "en" }) {
  const { t } = useI18n()
  const status = useQuery({ queryKey: ["tts-status"], queryFn: api.ttsStatus, staleTime: 600_000 })
  const [playing, setPlaying] = useState(false)
  const [loading, setLoading] = useState(false)  // server is synthesising / audio buffering — can take 10–20 s the first time
  const [err, setErr] = useState<string | null>(null)
  const [gender, setGender] = useState<"female" | "male">(() => { try { return (localStorage.getItem("instilens.tts") as "female" | "male") || "female" } catch { return "female" } })
  const pickGender = (g: "female" | "male") => { setGender(g); try { localStorage.setItem("instilens.tts", g) } catch { /* ignore */ } }
  const audio = useRef<HTMLAudioElement | null>(null)
  useEffect(() => () => { audio.current?.pause(); window.speechSynthesis?.cancel() }, [])

  const stop = () => { audio.current?.pause(); audio.current = null; window.speechSynthesis?.cancel(); setPlaying(false); setLoading(false) }
  const play = async () => {
    if (playing || loading) return stop()
    setErr(null)
    if (status.data?.provider) {
      const a = new Audio(api.noteAudioUrl(noteId, gender))
      audio.current = a
      const fail = () => { setPlaying(false); setLoading(false); setErr(t("tts.serverError", { p: status.data?.provider ?? "" })) }
      a.onplaying = () => { setLoading(false); setPlaying(true) }
      a.onended = () => setPlaying(false)
      a.onerror = fail
      setLoading(true)
      await a.play().catch(fail)
    } else speak()
  }
  const speak = () => {
    if (!("speechSynthesis" in window)) return
    const u = new SpeechSynthesisUtterance(text)
    const voices = window.speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith(lang))
    const v = voices.find((v) => (gender === "male") === /erkek|male|tolga|ahmet|daniel|david/i.test(v.name)) ?? voices[0]
    if (v) u.voice = v
    u.lang = lang === "en" ? "en-US" : "tr-TR"; u.rate = 1.0
    u.onend = () => setPlaying(false)
    setPlaying(true)
    window.speechSynthesis.speak(u)
  }
  const provider = status.data?.provider
  return (
    <span className="inline-flex items-center gap-1">
      <span className="inline-flex overflow-hidden rounded-md border border-border text-[11px]">
        <button onClick={() => pickGender("female")} className={`px-1.5 py-1 ${gender === "female" ? "bg-accent" : "text-muted-foreground"}`} title={t("tts.female")}>♀</button>
        <button onClick={() => pickGender("male")} className={`px-1.5 py-1 ${gender === "male" ? "bg-accent" : "text-muted-foreground"}`} title={t("tts.male")}>♂</button>
      </span>
      <button onClick={play} className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-xs hover:bg-accent" title={provider ? `${t("tts.title")} (${provider})` : `${t("tts.title")} (${t("tts.browser")})`}>
        {loading ? <Loader2 className="size-3.5 animate-spin" /> : playing ? <Pause className="size-3.5" /> : <Volume2 className="size-3.5" />} {loading ? t("tts.preparing") : playing ? t("tts.stop") : t("tts.listen")}
      </button>
      {!provider && status.data && <span className="text-[10px] text-warning" title={t("tts.noProvider")}>{t("tts.browser")}</span>}
      {err && <span className="text-[10px] text-negative">{err}</span>}
    </span>
  )
}
