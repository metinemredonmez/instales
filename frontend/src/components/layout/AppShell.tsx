import { NavLink } from "react-router-dom"
import { PanelLeftClose, PanelLeftOpen, Shield, Tv } from "lucide-react"
import { useEffect, useState } from "react"
import { cn } from "@/lib/utils"
import { Mark } from "@/components/layout/Brand"
import { SearchBox } from "@/components/layout/SearchBox"
import { QuoteStrip } from "@/components/layout/QuoteStrip"
import { MarketStatusPill } from "@/components/layout/MarketStatusPill"
import { ProfileMenu } from "@/components/layout/ProfileMenu"
import { CommandPalette } from "@/components/layout/CommandPalette"
import { ADMIN, MINE, NAV, type NavItem } from "@/components/layout/nav"
import { useMarket } from "@/lib/market"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import type { Key } from "@/i18n/tr"
import { NewsTicker } from "@/components/domain/NewsTicker"
import { BellMenu } from "@/components/domain/BellMenu"
import { UpdateBanner } from "@/components/domain/UpdateBanner"
import { LiveTvWidget } from "@/components/domain/LiveTvWidget"
import { registerSw } from "@/lib/push"
import { loadOneSignal, oneSignalExternalId, withOneSignal } from "@/lib/onesignal"
import { api } from "@/lib/api"

// ⌘ on Apple keyboards, Ctrl elsewhere — only the hint; the palette listens for both.
const PALETTE_KEY = typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K"

export function AppShell({ children }: { children: React.ReactNode }) {
  const { market, setMarket } = useMarket()
  const { user } = useAuth()
  const { lang, setLang, t } = useI18n()
  // One push path per deployment: OneSignal (its own worker + identity) when configured, else our /sw.js for VAPID.
  useEffect(() => {
    api.pushPublicKey().then((k) => {
      if (k.onesignal_app_id) {
        loadOneSignal(k.onesignal_app_id)
        if (user) withOneSignal((os) => os.login(oneSignalExternalId(user.id)))
      } else registerSw().catch(() => {})
    }).catch(() => {})
  }, [user])
  // The account's language wins on login; switching in the account menu saves it back.
  useEffect(() => { if (user?.lang && user.lang !== lang) setLang(user.lang) }, [user?.lang]) // eslint-disable-line react-hooks/exhaustive-deps
  // Sidebar: labels or icons only — the user's choice, remembered.
  const [wide, setWide] = useState(() => { try { return localStorage.getItem("instilens.sidebar") !== "icons" } catch { return true } })
  const toggleWide = () => setWide((w) => { try { localStorage.setItem("instilens.sidebar", w ? "icons" : "wide") } catch { /* ignore */ } return !w })
  // Live TV never auto-opens on load — only position/size/channel are remembered (in the widget itself).
  const [tv, setTv] = useState(false)
  const toggleTv = () => setTv((o) => !o)

  const item = ({ to, key, icon: Icon, end }: NavItem) => (
    <NavLink
      key={to}
      to={to}
      end={end}
      title={t(key)}
      className={({ isActive }) =>
        cn("flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition", isActive ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50 hover:text-foreground")
      }
    >
      <Icon className="size-4 shrink-0" /> {wide && <span className="hidden lg:inline">{t(key)}</span>}
    </NavLink>
  )

  // Group eyebrow: only when the sidebar shows labels; icon-only mode keeps just the divider.
  const group = (key: Key, first = false) => (
    <>
      {!first && <div className="my-2 border-t border-border/70" />}
      {wide && <div className={cn("hidden px-2.5 pb-1 text-[10px] font-medium uppercase tracking-wider text-muted-foreground/60 lg:block", first && "pt-1")}>{t(key)}</div>}
    </>
  )

  return (
    <div className="min-h-dvh bg-background text-foreground">
      <header className="sticky top-0 z-30 border-b border-border/70 bg-background/80 backdrop-blur">
        <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-2 px-3 sm:gap-3 sm:px-4">
          <NavLink to="/" className="flex shrink-0 items-center" aria-label={t("nav.home")} title="InstiLens">
            <Mark className="size-8" />
          </NavLink>

          <div className="flex shrink-0 rounded-md border border-border bg-card p-0.5 text-xs" role="tablist" aria-label={t("market.label")}>
            {([["TR", t("market.name.TR"), "KAP · " + t("market.funds")], ["US", t("market.name.US"), "SEC · 13F"]] as const).map(([m, label, hint]) => (
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
                <span className="hidden sm:inline">{label}</span>
              </button>
            ))}
          </div>

          <MarketStatusPill market={market} className="hidden md:inline-flex" />
          <QuoteStrip className="hidden lg:flex" />

          <SearchBox className="ml-auto w-full min-w-[120px] max-w-sm" shortcutHint={PALETTE_KEY} />

          <BellMenu />
          <ProfileMenu />
        </div>
      </header>
      <CommandPalette />
      <UpdateBanner />
      <LiveTvWidget open={tv} onClose={() => setTv(false)} />
      <NewsTicker market={market} />

      <div className="mx-auto flex max-w-[1600px]">
        {/* Left menu: icons on md, icons + labels on lg; sticky under the header. */}
        <aside className={cn("sticky top-14 hidden h-[calc(100dvh-3.5rem)] w-14 shrink-0 flex-col gap-1 overflow-y-auto border-r border-border/70 px-2 py-4 md:flex", wide && "lg:w-52 lg:px-3")}>
          {group("nav.group.market", true)}
          {NAV.map(item)}
          {group("nav.group.personal")}
          {MINE.map(item)}
          <button onClick={toggleTv} title={t("nav.liveTv")} className={cn("flex items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] transition", tv ? "bg-accent text-foreground" : "text-muted-foreground hover:bg-accent/50 hover:text-foreground")}>
            <Tv className="size-4 shrink-0" /> {wide && <span className="hidden lg:inline">{t("nav.liveTv")}</span>}
          </button>
          {user?.role === "ADMIN" && (
            <>
              {group("nav.group.system")}
              {item(ADMIN)}
            </>
          )}
          <button onClick={toggleWide} title={wide ? t("nav.collapse") : t("nav.expand")} aria-label={wide ? t("nav.collapse") : t("nav.expand")} className="mt-auto hidden items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] text-muted-foreground hover:bg-accent/50 hover:text-foreground lg:flex">
            {wide ? <PanelLeftClose className="size-4 shrink-0" /> : <PanelLeftOpen className="size-4 shrink-0" />}{wide && <span>{t("nav.collapse")}</span>}
          </button>
        </aside>

        <div className="min-w-0 flex-1">
          <main className="px-4 py-6 pb-24 md:pb-6">{children}</main>
          <footer className="px-4 pb-8 pt-2 text-xs text-muted-foreground">{t("footer.disclaimer")}</footer>
        </div>
      </div>

      {/* Phone: bottom bar with the essentials */}
      <nav className="fixed inset-x-0 bottom-0 z-30 flex border-t border-border bg-background/95 backdrop-blur md:hidden" aria-label={t("nav.home")}>
        {[...NAV, ...MINE].filter((n) => n.mobile).map(({ to, key, icon: Icon, end }) => (
          <NavLink key={to} to={to} end={end} className={({ isActive }) => cn("flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]", isActive ? "text-foreground" : "text-muted-foreground")}>
            <Icon className="size-5" />{t(key)}
          </NavLink>
        ))}
        {user?.role === "ADMIN" && <NavLink to="/admin" className={({ isActive }) => cn("flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px]", isActive ? "text-foreground" : "text-muted-foreground")}><Shield className="size-5" />{t("nav.admin")}</NavLink>}
      </nav>

    </div>
  )
}
