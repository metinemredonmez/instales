import { useQuery } from "@tanstack/react-query"
import { api } from "@/lib/api"
import { useI18n } from "@/lib/i18n"
import { tts, useTts } from "@/lib/tts"
import { cn } from "@/lib/utils"

/**
 * Voice + speed for narration. Lists only the voices of the given language (a Turkish note never offers English
 * voices and vice-versa); the choice is remembered per language. Speed is a playback multiplier.
 */
export function VoicePicker({ lang, compact }: { lang: "tr" | "en"; compact?: boolean }) {
  const { t } = useI18n()
  const s = useTts()
  const q = useQuery({ queryKey: ["tts-voices", lang], queryFn: () => api.ttsVoices(lang), staleTime: 600_000 })
  const voices = q.data?.voices ?? []
  const current = s.voice[lang] ?? voices.find((v) => v.gender === s.gender && v.source === "config")?.id ?? ""
  const sel = "h-7 rounded-md border border-border bg-card px-1.5 text-[11px] outline-none hover:bg-accent"
  return (
    <span className={cn("inline-flex items-center gap-1", compact && "gap-0.5")}>
      {voices.length > 0 ? (
        <select value={current} onChange={(e) => { const v = voices.find((x) => x.id === e.target.value); tts.setVoice(lang, v?.source === "config" ? null : e.target.value, v?.gender) }} className={cn(sel, "max-w-[150px]")} title={t("tts.voice")} aria-label={t("tts.voice")}>
          {voices.map((v) => <option key={v.id} value={v.id}>{v.gender === "male" ? "♂" : "♀"} {v.name}{v.source === "library" ? " ·" : ""}</option>)}
        </select>
      ) : (
        <span className="inline-flex overflow-hidden rounded-md border border-border text-[11px]">
          <button onClick={() => tts.setGender("female")} className={`px-1.5 py-1 ${s.gender === "female" ? "bg-accent" : "text-muted-foreground"}`} title={t("tts.female")}>♀</button>
          <button onClick={() => tts.setGender("male")} className={`px-1.5 py-1 ${s.gender === "male" ? "bg-accent" : "text-muted-foreground"}`} title={t("tts.male")}>♂</button>
        </span>
      )}
      <select value={String(s.rate)} onChange={(e) => tts.setRate(Number(e.target.value))} className={sel} title={t("tts.speed")} aria-label={t("tts.speed")}>
        {[0.8, 0.9, 1, 1.1, 1.25].map((r) => <option key={r} value={String(r)}>{r}×</option>)}
      </select>
    </span>
  )
}
