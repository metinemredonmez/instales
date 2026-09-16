import { NavLink } from "react-router-dom"
import { Activity, Building2, GitCompare, LogOut, Moon, Radar, Shield, SlidersHorizontal, Sparkles, Star, Sun } from "lucide-react"
import { Button } from "@/components/ui/button"
import { cn } from "@/lib/utils"
import { Mark, Wordmark } from "@/components/layout/Brand"
import { SearchBox } from "@/components/layout/SearchBox"
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
  return (
    <div className="min-h-dvh bg-background text-foreground">
      <header className="sticky top-0 z-30 border-b border-border/70 bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1400px] items-center gap-4 px-4">
          <NavLink to="/" className="flex items-center gap-2.5" aria-label="InstiLens — ana sayfa">
            <Mark />
            <Wordmark className="hidden h-[13px] lg:block" />
          </NavLink>

          <div className="flex rounded-md border border-border bg-card p-0.5 text-xs" role="tablist" aria-label="Piyasa">
            {([["TR", "BIST", "KAP · fonlar"], ["US", "Global", "SEC · 13F"]] as const).map(([m, label, hint]) => (
              <button
                key={m}
                role="tab"
                aria-selected={market === m}
                onClick={() => setMarket(m)}
                title={hint}
                className={cn(
                  "flex items-center gap-1.5 rounded-[5px] px-2.5 py-1 font-medium transition",
                  market === m ? "bg-secondary text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                )}
              >
                <span className={cn("rounded-sm px-1 py-px font-mono text-[10px] tracking-wider", market === m ? "bg-primary text-primary-foreground" : "bg-muted text-muted-foreground")}>{m}</span>
                {label}
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

          <SearchBox className="ml-auto w-full max-w-xs" />

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
