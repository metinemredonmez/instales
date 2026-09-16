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
}

const GENDER_KEY = "instilens.tts"
const initialGender = (): TtsGender => { try { return (localStorage.getItem(GENDER_KEY) as TtsGender) || "female" } catch { return "female" } }

let state: TtsState = { status: "idle", noteId: null, title: "", lang: "tr", gender: initialGender(), provider: null, progress: null, error: null }
const listeners = new Set<() => void>()
const set = (patch: Partial<TtsState>) => { state = { ...state, ...patch }; listeners.forEach((l) => l()) }
const subscribe = (l: () => void) => { listeners.add(l); return () => { listeners.delete(l) } }

let audio: HTMLAudioElement | null = null
let utterance: SpeechSynthesisUtterance | null = null

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
  play(opts: { noteId: number; title: string; text: string; lang: "tr" | "en"; provider: string | null; errorMessage: (provider: string) => string }) {
    clearMedia()
    const { noteId, title, text, lang, provider } = opts
    const mine = () => state.noteId === noteId
    if (provider) {
      const a = new Audio(api.noteAudioUrl(noteId, state.gender))
      audio = a
      set({ status: "preparing", noteId, title, lang, provider, progress: null, error: null })
      // Keep noteId so the button that started this shows the error; the widget hides on "idle".
      const fail = () => { if (!mine()) return; clearMedia(); set({ status: "idle", progress: null, error: opts.errorMessage(provider) }) }
      a.onplaying = () => mine() && set({ status: "playing", error: null })
      a.onended = () => mine() && tts.stop()
      a.onerror = fail
      a.ontimeupdate = () => { if (mine() && Number.isFinite(a.duration) && a.duration > 0) set({ progress: a.currentTime / a.duration }) }
      a.play().catch(fail)
      return
    }
    if (!("speechSynthesis" in window)) return
    const u = new SpeechSynthesisUtterance(text)
    const voices = window.speechSynthesis.getVoices().filter((v) => v.lang.toLowerCase().startsWith(lang))
    const v = voices.find((v) => (state.gender === "male") === /erkek|male|tolga|ahmet|daniel|david/i.test(v.name)) ?? voices[0]
    if (v) u.voice = v
    u.lang = lang === "en" ? "en-US" : "tr-TR"; u.rate = 1.0
    u.onend = () => mine() && tts.stop()
    u.onerror = () => mine() && tts.stop()
    u.onboundary = (e) => { if (mine() && text.length) set({ progress: Math.min(1, e.charIndex / text.length) }) }
    utterance = u
    set({ status: "playing", noteId, title, lang, provider: null, progress: null, error: null })
    window.speechSynthesis.speak(u)
  },
}

export const useTts = () => useSyncExternalStore(subscribe, () => state, () => state)
