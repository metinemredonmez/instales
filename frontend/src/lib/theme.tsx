import { createContext, useContext, useEffect, useState, type ReactNode } from "react"

type Theme = "dark" | "light"
const Ctx = createContext<{ theme: Theme; toggle: () => void }>({ theme: "dark", toggle: () => {} })

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setTheme] = useState<Theme>(() => {
    try {
      return (localStorage.getItem("instilens.theme") as Theme) || "dark"
    } catch {
      return "dark"
    }
  })
  useEffect(() => {
    document.documentElement.classList.toggle("dark", theme === "dark")
    try {
      localStorage.setItem("instilens.theme", theme)
    } catch {
      /* private mode */
    }
  }, [theme])
  return <Ctx.Provider value={{ theme, toggle: () => setTheme((t) => (t === "dark" ? "light" : "dark")) }}>{children}</Ctx.Provider>
}

export const useTheme = () => useContext(Ctx)
