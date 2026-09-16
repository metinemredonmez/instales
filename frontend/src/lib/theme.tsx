import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

type Theme = "dark" | "light"
const KEY = "instilens.theme"
const Ctx = createContext<{ theme: Theme; toggle: () => void }>({ theme: "dark", toggle: () => {} })

const stored = (): Theme | null => {
  try {
    const v = localStorage.getItem(KEY)
    return v === "dark" || v === "light" ? v : null
  } catch {
    return null
  }
}
const system = (): Theme => (typeof window !== "undefined" && window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light")

/** Theme: the user's explicit choice wins; until they pick one, follow the OS (and track its changes). */
export function ThemeProvider({ children }: { children: ReactNode }) {
  const [chosen, setChosen] = useState<Theme | null>(stored)
  const [sys, setSys] = useState<Theme>(system)
  const theme = chosen ?? sys
  useEffect(() => {
    const mq = window.matchMedia?.("(prefers-color-scheme: dark)")
    if (!mq) return
    const onChange = (e: MediaQueryListEvent) => setSys(e.matches ? "dark" : "light")
    mq.addEventListener?.("change", onChange)
    return () => mq.removeEventListener?.("change", onChange)
  }, [])
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
  }, [theme])
  useEffect(() => {
    if (!chosen) return
    try {
      localStorage.setItem(KEY, chosen)
    } catch {
      /* private mode */
    }
  }, [chosen])
  return <Ctx.Provider value={{ theme, toggle: () => setChosen(theme === "dark" ? "light" : "dark") }}>{children}</Ctx.Provider>
}

export const useTheme = () => useContext(Ctx)
