import { NavLink, useNavigate } from "react-router-dom"
import { Activity, Building2, GitCompare, LogOut, Moon, Radar, Search, Shield, SlidersHorizontal, Sparkles, Star, Sun } from "lucide-react"
import { useState, type FormEvent } from "react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { useTheme } from "@/lib/theme"
import { useMarket } from "@/lib/market"
import { useAuth } from "@/lib/auth"
import { NewsTicker } from "@/components/domain/NewsTicker"
import { BellMenu } from "@/components/domain/BellMenu"
import { registerSw } from "@/lib/push"
import { loadOneSignal, withOneSignal } from "@/lib/onesignal"
import { api } from "@/lib/api"
import { useEffect } from "react"

const NAV = [
  { to: "/", label: "Radar", icon: Radar, end: true },
  { to: "/live", label: "Live", icon: Activity },
  { to: "/screener", label: "Screener", icon: SlidersHorizontal },
  { to: "/institutions", label: "Kurumlar", icon: Building2 },
  { to: "/compare", label: "Karşılaştır", icon: GitCompare },
  { to: "/research", label: "Research", icon: Sparkles },
]

export function AppShell({ children }: { children: React.ReactNode }) {
  const { theme, toggle } = useTheme()
  const { market, setMarket } = useMarket()
  const { user, logout } = useAuth()
  useEffect(() => { registerSw().catch(() => {}) }, [])
  useEffect(() => {
    api.pushPublicKey().then((k) => {
      if (!k.onesignal_app_id || !user) return
      loadOneSignal(k.onesignal_app_id)
      withOneSignal((os) => os.login(String(user.id)))
    }).catch(() => {})
  }, [user])
  const navigate = useNavigate()
  const [q, setQ] = useState("")

  const submit = (e: FormEvent) => {
    e.preventDefault()
    const s = q.trim().toUpperCase()
    if (!s) return
    // TEFAS fund codes are 3 chars; BIST tickers are 4-5. Good enough until we have a search endpoint.
    navigate(s.length === 3 ? `/funds/${s}` : `/stocks/${s}`)
    setQ("")
  }

  return (
    <div className="min-h-dvh bg-background text-foreground">
      <header className="sticky top-0 z-30 border-b border-border/70 bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-4 px-4">
          <NavLink to="/" className="flex items-center gap-2">
            <span className="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground text-xs font-black">IL</span>
            <span className="hidden text-sm font-semibold tracking-tight lg:inline">InstiLens</span>
          </NavLink>

          <div className="flex rounded-md border border-border p-0.5 text-xs">
            {(["TR", "US"] as const).map((m) => (
              <button
                key={m}
                onClick={() => setMarket(m)}
                className={cn("rounded-[5px] px-2.5 py-1 font-medium transition", market === m ? "bg-secondary text-foreground" : "text-muted-foreground hover:text-foreground")}
              >
                {m === "TR" ? "🇹🇷 Türkiye" : "🌎 Global"}
              </button>
            ))}
          </div>

          <nav className="hidden items-center gap-0.5 md:flex">
            {NAV.map(({ to, label, icon: Icon, end }) => (
              <NavLink
                key={to}
                to={to}
                end={end}
                className={({ isActive }) =>
                  cn("flex items-center gap-1.5 whitespace-nowrap rounded-md px-2 py-1.5 text-[13px] transition", isActive ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")
                }
              >
                <Icon className="size-4" /> <span className="hidden lg:inline">{label}</span>
              </NavLink>
            ))}
          </nav>

          <form onSubmit={submit} className="ml-auto relative w-full max-w-xs">
            <Search className="pointer-events-none absolute left-2.5 top-1/2 size-4 -translate-y-1/2 text-muted-foreground" />
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="Hisse veya fon: ASELS, TMV…"
              className="h-9 w-full rounded-md border border-input bg-card pl-9 pr-3 text-sm outline-none placeholder:text-muted-foreground focus:ring-2 focus:ring-ring/40"
            />
          </form>

          {user?.role === "ADMIN" && <NavLink to="/admin" aria-label="Admin" className={({ isActive }) => cn("grid size-9 place-items-center rounded-md hover:bg-accent", isActive && "bg-accent")}><Shield className="size-4" /></NavLink>}
          <NavLink to="/watchlist" aria-label="Watchlist" className={({ isActive }) => cn("grid size-9 place-items-center rounded-md hover:bg-accent", isActive && "bg-accent")}><Star className="size-4" /></NavLink>
          <BellMenu />
          <Button variant="ghost" size="icon" aria-label="Tema" onClick={toggle}>
            {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          </Button>
          <div className="ml-1 hidden items-center gap-2 border-l border-border pl-3 sm:flex">
            <div className="leading-tight">
              <div className="text-xs font-medium">{user?.name}</div>
              <div className="text-[10px] uppercase tracking-wider text-muted-foreground">{user?.plan}</div>
            </div>
            <Button variant="ghost" size="icon" aria-label="Çıkış" onClick={() => { withOneSignal((os) => os.logout()); logout() }}><LogOut className="size-4" /></Button>
          </div>
        </div>
      </header>
      <NewsTicker market={market} />
      <main className="mx-auto max-w-[1400px] px-4 py-6">{children}</main>
      <footer className="mx-auto max-w-[1400px] px-4 pb-8 pt-4 text-xs text-muted-foreground">
        Veriler kamuya açık düzenleyici bildirimlerden (KAP, SEC) türetilmiştir; yatırım tavsiyesi değildir. Her rakam kaynağına kadar izlenebilir.
      </footer>
    </div>
  )
}
