import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"
import { tr, type Key } from "@/i18n/tr"
import { en } from "@/i18n/en"
import { setLocale } from "@/lib/format"

export type Lang = "tr" | "en"
export type Vars = Record<string, string | number>
export type T = (key: Key, vars?: Vars) => string

const KEY = "instilens.lang"
const DICT: Record<Lang, Record<Key, string>> = { tr, en }

export function translate(lang: Lang, key: Key, vars?: Vars): string {
  let s = DICT[lang][key] ?? tr[key] ?? key
  if (vars) for (const [k, v] of Object.entries(vars)) s = s.replaceAll(`{${k}}`, String(v))
  return s
}

function initial(): Lang {
  try {
    const s = localStorage.getItem(KEY)
    if (s === "tr" || s === "en") return s
  } catch { /* private mode */ }
  return typeof navigator !== "undefined" && navigator.language?.toLowerCase().startsWith("tr") ? "tr" : "en"
}

const Ctx = createContext<{ lang: Lang; setLang: (l: Lang) => void; t: T }>({ lang: "tr", setLang: () => {}, t: (k, v) => translate("tr", k, v) })

/** UI language. Persisted locally; AppShell syncs it with the account (server wins on login). */
export function LangProvider({ children }: { children: ReactNode }) {
  const [lang, setLang] = useState<Lang>(initial)
  setLocale(lang) // synchronous so formatters are right on the very first render
  useEffect(() => {
    try { localStorage.setItem(KEY, lang) } catch { /* ignore */ }
    document.documentElement.lang = lang
  }, [lang])
  const t = useCallback<T>((k, v) => translate(lang, k, v), [lang])
  const value = useMemo(() => ({ lang, setLang, t }), [lang, t])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useI18n = () => useContext(Ctx)
