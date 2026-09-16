import { useSyncExternalStore } from "react"
import { api } from "@/lib/api"

/**
 * One playback state for narration. The audio element / speech utterance lives here, outside React, so a note keeps
 * playing while the user moves between pages; SpeakButton and the NowSpeaking widget both read this same store.
 */
export type TtsGender = "female" | "male"
export type TtsStatus = "idle" | "preparing" | "playing" | "paused"
export interface TtsState {
  status: TtsStatus
  noteId: number | null
  title: string
  lang: "tr" | "en"
  gender: TtsGender
  /** Server provider name (elevenlabs/openai) or null for the browser voice. */
  provider: string | null
  /** 0..1 while known (server audio: time/duration; browser voice: spoken characters/total). null = unknown. */
  progress: number | null
  /** Provider name that failed, shown next to the button that started it. */
  error: string | null
  /** Chosen ElevenLabs voice per language (null = the configured default for the gender). */
  voice: Record<"tr" | "en", string | null>
  /** Playback speed multiplier (0.75–1.25); applied to server audio and the browser voice. */
  rate: number
}

const GENDER_KEY = "instilens.tts"
const VOICE_KEY = "instilens.tts.voice"
const RATE_KEY = "instilens.tts.rate"
const initialGender = (): TtsGender => { try { return (localStorage.getItem(GENDER_KEY) as TtsGender) || "female" } catch { return "female" } }
const initialVoice = (): Record<"tr" | "en", string | null> => { try { return { tr: null, en: null, ...JSON.parse(localStorage.getItem(VOICE_KEY) || "{}") } } catch { return { tr: null, en: null } } }
const initialRate = (): number => { try { const r = Number(localStorage.getItem(RATE_KEY)); return r >= 0.75 && r <= 1.25 ? r : 1 } catch { return 1 } }

let state: TtsState = { status: "idle", noteId: null, title: "", lang: "tr", gender: initialGender(), provider: null, progress: null, error: null, voice: initialVoice(), rate: initialRate() }
const listeners = new Set<() => void>()
const set = (patch: Partial<TtsState>) => { state = { ...state, ...patch }; listeners.forEach((l) => l()) }
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }

let audio: HTMLAudioElement | null = null
let utterance: SpeechSynthesisUtterance | null = null
type PlayOpts = { noteId: number; title: string; text: string; lang: "tr" | "en"; provider: string | null; errorMessage: (provider: string) => string }
let lastOpts: PlayOpts | null = null
let preview: HTMLAudioElement | null = null

const clearMedia = () => {
  if (audio) { audio.onplaying = audio.onended = audio.onerror = audio.ontimeupdate = audio.onpause = null; audio.pause(); audio = null }
  if (utterance) { utterance.onend = utterance.onboundary = utterance.onerror = null; utterance = null }
  window.speechSynthesis?.cancel()
}

export const tts = {
  setGender(g: TtsGender) {
    set({ gender: g })
    try { localStorage.setItem(GENDER_KEY, g) } catch { /* ignore */ }
  },
  /** Pick a voice for a language (null = default for the gender). If that language is playing now, the new voice
   *  takes over immediately, resuming at the same position when the file is seekable (cached). */
  setVoice(lang: "tr" | "en", id: string | null, gender?: TtsGender) {
    const voice = { ...state.voice, [lang]: id }
    set({ voice, ...(gender ? { gender } : {}) })
    try { localStorage.setItem(VOICE_KEY, JSON.stringify(voice)); if (gender) localStorage.setItem(GENDER_KEY, gender) } catch { /* ignore */ }
    if (lastOpts && state.noteId === lastOpts.noteId && state.status !== "idle" && lastOpts.lang === lang && lastOpts.provider) {
      const at = audio?.currentTime ?? 0
      tts.play(lastOpts, at)
    }
  },
  /** Audition a voice with a short sample (stops any running preview; does not touch the note playback state). */
  previewVoice(lang: "tr" | "en", id: string): Promise<void> {
    if (preview) { preview.pause(); preview = null }
    return api.ticket().then(({ ticket }) => {
      const a = new Audio(api.ttsPreviewUrl(id, lang, ticket))
      a.playbackRate = state.rate
      preview = a
      return a.play().catch(() => undefined)
    })
  },
  stopPreview() { if (preview) { preview.pause(); preview = null } },
  setRate(r: number) {
    const rate = Math.min(1.25, Math.max(0.75, r))
    set({ rate })
    if (audio) audio.playbackRate = rate
    try { localStorage.setItem(RATE_KEY, String(rate)) } catch { /* ignore */ }
  },
  stop() { clearMedia(); set({ status: "idle", noteId: null, title: "", progress: null }) },
  pause() {
    if (state.status !== "playing") return
    if (audio) audio.pause()
    else window.speechSynthesis?.pause()
    set({ status: "paused" })
  },
  resume() {
    if (state.status !== "paused") return
    if (audio) audio.play().catch(() => tts.stop())
    else window.speechSynthesis?.resume()
    set({ status: "playing" })
  },
  toggle() { if (state.status === "playing") tts.pause(); else if (state.status === "paused") tts.resume() },
  /** Start reading a note; whatever was playing before is stopped first. */
  play(opts: PlayOpts, startAt = 0) {
    clearMedia()
    lastOpts = opts
    const { noteId, title, text, lang, provider } = opts
    const mine = () => state.noteId === noteId
    if (provider) {
      set({ status: "preparing", noteId, title, lang, provider, progress: null, error: null })
      // Keep noteId so the button that started this shows the error; the widget hides on "idle".
      const fail = () => { if (!mine()) return; clearMedia(); set({ status: "idle", progress: null, error: opts.errorMessage(provider) }) }
      // The audio URL carries a 5-minute ticket, never the session token (it would end up in access logs).
      api.ticket().then(({ ticket }) => {
        if (!mine()) return
        const a = new Audio(api.noteAudioUrl(noteId, state.gender, ticket, state.voice[lang]))
        a.playbackRate = state.rate
        audio = a
        a.onloadedmetadata = () => { if (startAt > 0 && Number.isFinite(a.duration) && a.duration > startAt) a.currentTime = startAt }
        a.onplaying = () => mine() && set({ status: "playing", error: null })
        a.onended = () => mine() && tts.stop()
        a.onerror = fail
        a.ontimeupdate = () => { if (mine() && Number.isFinite(a.duration) && a.duration > 0) set({ progress: a.currentTime / a.duration }) }
        a.play().catch(fail)
      }).catch(fail)
      return
    }
    if (!("speechSynthesis" in window)) return
    const u = new SpeechSynthesisUtterance(text)
    const voices = window.speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith(lang))
    const v = voices.find((v) => (state.gender === "male") === /erkek|male|tolga|ahmet|daniel|david/i.test(v.name)) ?? voices[0]
    if (v) u.voice = v
    u.lang = lang === "en" ? "en-US" : "tr-TR"; u.rate = state.rate
    u.onend = () => mine() && tts.stop()
    u.onerror = () => mine() && tts.stop()
    u.onboundary = (e) => { if (mine() && text.length) set({ progress: Math.min(1, e.charIndex / text.length) }) }
    utterance = u
    set({ status: "playing", noteId, title, lang, provider: null, progress: null, error: null })
    window.speechSynthesis.speak(u)
  },
}

export const useTts = () => useSyncExternalStore(subscribe, () => state, () => state)
