import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react"

export interface User { id: number; email: string; name: string; plan: "FREE" | "PRO" | "PRO_PLUS"; role?: "USER" | "ADMIN"; lang?: "tr" | "en"; email_verified?: boolean; mfa_enabled?: boolean }
export interface Session { access_token: string; user: User }
/** Login answer when the account has TOTP enabled: no session yet, a short-lived token for the code step. */
export interface MfaChallenge { mfa_required: true; mfa_token: string }

const KEY = "instilens.session"
const AUTH_BASE = `${import.meta.env.VITE_API_BASE ?? ""}/api/v1/auth`
const Ctx = createContext<{
  user: User | null
  token: string | null
  login: (email: string, password: string) => Promise<MfaChallenge | null>
  verifyMfa: (mfa_token: string, code: string) => Promise<void>
  register: (email: string, password: string, name: string) => Promise<void>
  resetPassword: (token: string, new_password: string) => Promise<void>
  refreshUser: () => Promise<void>
  logout: () => void
  adoptSession: (s: Session) => void
}>({ user: null, token: null, login: async () => null, verifyMfa: async () => {}, register: async () => {}, resetPassword: async () => {}, refreshUser: async () => {}, logout: () => {}, adoptSession: () => {} })

let currentToken: string | null = null
export const getToken = () => currentToken

/** Unauthenticated auth call; surfaces the API's `detail` as the error message (no 401 → logout side effect). */
export async function authPost<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${AUTH_BASE}/${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) })
  if (!r.ok) {
    const detail = (await r.json().catch(() => ({}))).detail
    throw new Error(typeof detail === "string" ? detail : Array.isArray(detail) ? detail[0]?.msg ?? "Hata" : "Hata")
  }
  return r.json()
}

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

  // Re-read the account (plan/role/email_verified may have changed); a stale token logs the user out cleanly.
  const refreshUser = useCallback(async () => {
    const token = currentToken
    if (!token) return
    const r = await fetch(`${AUTH_BASE}/me`, { headers: { authorization: `Bearer ${token}` } })
    if (r.status === 401) { setSession(null); return }
    if (!r.ok) return
    const user = (await r.json()) as User
    setSession((s) => (s && s.access_token === token ? { ...s, user: { ...s.user, ...user } } : s))
  }, [])

  // Validate the stored token once on load.
  useEffect(() => {
    if (!session) return
    refreshUser().catch(() => {})
  }, [])  // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    const onUnauthorized = () => setSession(null)
    window.addEventListener("instilens:unauthorized", onUnauthorized)
    return () => window.removeEventListener("instilens:unauthorized", onUnauthorized)
  }, [])

  const login = useCallback(async (email: string, password: string) => {
    const r = await authPost<Session | MfaChallenge>("login", { email, password })
    if ("mfa_required" in r && r.mfa_required) return r
    setSession(r as Session)
    return null
  }, [])
  const verifyMfa = useCallback(async (mfa_token: string, code: string) => setSession(await authPost<Session>("mfa/verify", { mfa_token, code })), [])
  const register = useCallback(async (email: string, password: string, name: string) => setSession(await authPost<Session>("register", { email, password, name })), [])
  const resetPassword = useCallback(async (token: string, new_password: string) => setSession(await authPost<Session>("reset", { token, new_password })), [])
  const logout = useCallback(() => setSession(null), [])
  const adoptSession = useCallback((s: Session) => setSession(s), [])

  const value = useMemo(
    () => ({ user: session?.user ?? null, token: session?.access_token ?? null, login, verifyMfa, register, resetPassword, refreshUser, logout, adoptSession }),
    [session, login, verifyMfa, register, resetPassword, refreshUser, logout, adoptSession],
  )
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>
}

export const useAuth = () => useContext(Ctx)
