import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"

export interface User { id: number; email: string; name: string; plan: "FREE" | "PRO" | "PRO_PLUS"; role?: "USER" | "ADMIN"; lang?: "tr" | "en" }
interface Session { access_token: string; user: User }

const KEY = "instilens.session"
const Ctx = createContext<{
  user: User | null
  token: string | null
  login: (email: string, password: string) => Promise<void>
  register: (email: string, password: string, name: string) => Promise<void>
  logout: () => void
  adoptSession: (s: Session) => void
}>({ user: null, token: null, login: async () => {}, register: async () => {}, logout: () => {}, adoptSession: () => {} })

let currentToken: string | null = null
export const getToken = () => currentToken

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(() => {
    try {
      const raw = localStorage.getItem(KEY)
      return raw ? (JSON.parse(raw) as Session) : null
    } catch {
      return null
    }
  })
  currentToken = session?.access_token ?? null

  useEffect(() => {
    try {
      session ? localStorage.setItem(KEY, JSON.stringify(session)) : localStorage.removeItem(KEY)
    } catch { /* private mode */ }
    currentToken = session?.access_token ?? null
  }, [session])

  // Validate the stored token once on load; a stale token logs the user out cleanly.
  useEffect(() => {
    if (!session) return
    fetch(`${import.meta.env.VITE_API_BASE ?? ""}/api/v1/auth/me`, { headers: { authorization: `Bearer ${session.access_token}` } }).then((r) => {
      if (r.status === 401) setSession(null)
    }).catch(() => {})
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onUnauthorized = () => setSession(null)
    window.addEventListener("instilens:unauthorized", onUnauthorized)
    return () => window.removeEventListener("instilens:unauthorized", onUnauthorized)
  }, [])

  const post = async (path: string, body: unknown): Promise<Session> => {
    const r = await fetch(`${import.meta.env.VITE_API_BASE ?? ""}/api/v1/auth/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
    if (!r.ok) {
      const detail = (await r.json().catch(() => ({}))).detail
      throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail[0]?.msg ?? "Hata" : "Hata")
    }
    return r.json()
  }
  const login = useCallback(async (email: string, password: string) => setSession(await post("login", { email, password })), [])
  const register = useCallback(async (email: string, password: string, name: string) => setSession(await post("register", { email, password, name })), [])
  const logout = useCallback(() => setSession(null), [])
  const adoptSession = useCallback((s: Session) => setSession(s), [])

  const value = useMemo(() => ({ user: session?.user ?? null, token: session?.access_token ?? null, login, register, logout, adoptSession }), [session, login, register, logout, adoptSession])
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
