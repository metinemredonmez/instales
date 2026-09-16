import { useQuery } from "@tanstack/react-query"
import { Pause, Volume2 } from "lucide-react"
import { useEffect, useRef, useState } from "react"
import { api } from "@/lib/api"

/** Read an AI note aloud: server TTS (ElevenLabs/OpenAI) when configured, else the browser's Turkish voice. */
export function SpeakButton({ noteId, text }: { noteId: number; text: string }) {
  const status = useQuery({ queryKey: ["tts-status"], queryFn: api.ttsStatus, staleTime: 600_000 })
  const [playing, setPlaying] = useState(false)
  const [gender, setGender] = useState<"female" | "male">(() => { try { return (localStorage.getItem("instilens.tts") as "female" | "male") || "female" } catch { return "female" } })
  const pickGender = (g: "female" | "male") => { setGender(g); try { localStorage.setItem("instilens.tts", g) } catch { /* ignore */ } }
  const audio = useRef<HTMLAudioElement | null>(null)
  useEffect(() => () => { audio.current?.pause(); window.speechSynthesis?.cancel() }, [])

  const stop = () => { audio.current?.pause(); audio.current = null; window.speechSynthesis?.cancel(); setPlaying(false) }
  const play = async () => {
    if (playing) return stop()
    if (status.data?.provider) {
      const a = new Audio(api.noteAudioUrl(noteId, gender))
      audio.current = a
      a.onended = () => setPlaying(false)
      a.onerror = () => { setPlaying(false); speak() }
      setPlaying(true)
      await a.play().catch(() => { setPlaying(false); speak() })
    } else speak()
  }
  const speak = () => {
    if (!("speechSynthesis" in window)) return
    const u = new SpeechSynthesisUtterance(text)
    const voices = window.speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith("tr"))
    const tr = voices.find((v) => (gender === "male") === /erkek|male|tolga|ahmet/i.test(v.name)) ?? voices[0]
    if (tr) u.voice = tr
    u.lang = "tr-TR"; u.rate = 1.0
    u.onend = () => setPlaying(false)
    setPlaying(true)
    window.speechSynthesis.speak(u)
  }
  return (
    <span className="inline-flex items-center gap-1">
    <span className="inline-flex overflow-hidden rounded-md border border-border text-[11px]">
      <button onClick={() => pickGender("female")} className={`px-1.5 py-1 ${gender === "female" ? "bg-accent" : "text-muted-foreground"}`} title="Kadın ses">♀</button>
      <button onClick={() => pickGender("male")} className={`px-1.5 py-1 ${gender === "male" ? "bg-accent" : "text-muted-foreground"}`} title="Erkek ses">♂</button>
    </span>
    <button onClick={play} className="inline-flex items-center gap-1 rounded-md border border-border bg-card px-2 py-1 text-xs hover:bg-accent" title={status.data?.provider ? `Sesli anlatım (${status.data.provider})` : "Sesli anlatım (tarayıcı sesi)"}>
      {playing ? <Pause className="size-3.5" /> : <Volume2 className="size-3.5" />} {playing ? "Durdur" : "Dinle"}
    </button>
    </span>
  )
}
