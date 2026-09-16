import { useCallback } from "react"
import { api } from "@/lib/api"
import { useI18n, type Lang } from "@/lib/i18n"
import { locale } from "@/lib/format"

/** Switch the UI language and save it to the account (the server copy wins on the next login). */
export function usePickLang() {
  const { setLang } = useI18n()
  return useCallback((l: Lang) => { setLang(l); api.saveSettings({ lang: l }).catch(() => {}) }, [setLang])
}

/** "Emre Dönmez" → "ED"; a single word gives its first two letters; falls back to the e-mail's first letter. Upper-cased in the UI locale ("i" → "İ" only in Turkish). */
export function initials(name: string | undefined, email: string | undefined): string {
  const words = (name ?? "").trim().split(/\s+/).filter(Boolean)
  const up = (s: string) => s.toLocaleUpperCase(locale())
  if (words.length >= 2) return up(words[0][0] + words[words.length - 1][0])
  if (words.length === 1) return up(words[0].slice(0, 2))
  return up((email ?? "?").slice(0, 1))
}
