import { useEffect, useId, useRef, useState } from "react"
import { NavLink } from "react-router-dom"
import { LogOut, MailWarning, Monitor, Moon, Settings, Sun, SunMoon } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import { useTheme, type ThemeMode } from "@/lib/theme"
import { withOneSignal } from "@/lib/onesignal"
import { cn } from "@/lib/utils"
import type { Key } from "@/i18n/tr"
import { QuoteStrip } from "./QuoteStrip"
import { MarketStatusPill } from "./MarketStatusPill"
import { initials, usePickLang } from "./prefs"

const PLAN: Record<"FREE" | "PRO" | "PRO_PLUS", Key> = { FREE: "plan.free", PRO: "plan.pro", PRO_PLUS: "plan.proPlus" }
const THEMES: { mode: ThemeMode; key: Key; icon: React.ComponentType<{ className?: string }> }[] = [
  { mode: "dark", key: "menu.theme.dark", icon: Moon },
  { mode: "light", key: "menu.theme.light", icon: Sun },
  { mode: "system", key: "menu.theme.system", icon: SunMoon },
]

/**
 * Avatar (initials) → account panel: who is signed in, the quotes + market clocks that do not fit the header on
 * small screens, then Settings, Desktop app, language, theme, sign out. A labelled non-modal dialog rather than a
 * `menu` (its language/theme segments are radio groups, which a menu may not contain); focus moves into the panel
 * when it opens and returns to the avatar on Escape. Closes on outside click, Escape, or when focus leaves it.
 */
export function ProfileMenu() {
  const { user, logout } = useAuth()
  const { lang, t } = useI18n()
  const { mode, setMode } = useTheme()
  const pickLang = usePickLang()
  const [open, setOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const btnRef = useRef<HTMLButtonElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const panelId = useId()

  useEffect(() => {
    if (!open) return
    panelRef.current?.focus()
    const onDoc = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false) }
    // Radix dialogs (⌘K) consume Escape in the capture phase; a prevented event was theirs, not this panel's.
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape" && !e.defaultPrevented) { setOpen(false); btnRef.current?.focus() } }
    document.addEventListener("mousedown", onDoc)
    document.addEventListener("keydown", onKey)
    return () => { document.removeEventListener("mousedown", onDoc); document.removeEventListener("keydown", onKey) }
  }, [open])
  // Tabbing (or clicking) out of the panel closes it; moving between its own controls does not.
  const onBlur = (e: React.FocusEvent<HTMLDivElement>) => { if (open && e.relatedTarget && !ref.current?.contains(e.relatedTarget as Node)) setOpen(false) }

  const plan = user?.role === "ADMIN" ? t("plan.admin") : t(PLAN[user?.plan ?? "FREE"])
  const item = "flex w-full items-center gap-2.5 rounded-md px-2.5 py-2 text-[13px] text-foreground hover:bg-accent focus-visible:bg-accent focus-visible:outline-none"
  const seg = (on: boolean) => cn("flex flex-1 items-center justify-center gap-1 rounded-[5px] px-2 py-1 text-[11px] font-medium transition", on ? "bg-secondary text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground")

  return (
    <div ref={ref} onBlur={onBlur} className="relative shrink-0">
      <button
        ref={btnRef}
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="dialog"
        aria-controls={open ? panelId : undefined}
        aria-expanded={open}
        aria-label={t("menu.account")}
        title={user?.name}
        className={cn("grid size-9 place-items-center rounded-full border border-border bg-card text-xs font-semibold tracking-wide text-foreground transition hover:bg-accent", open && "bg-accent")}
      >
        {initials(user?.name, user?.email)}
        {user?.email_verified === false && <span className="absolute -right-0.5 -top-0.5 size-2.5 rounded-full border-2 border-background bg-warning" aria-hidden />}
      </button>
      {open && (
        <div ref={panelRef} id={panelId} tabIndex={-1} role="dialog" aria-label={t("menu.account")} className="rise absolute right-0 top-full z-50 mt-1 w-[min(300px,calc(100vw-2rem))] rounded-lg border border-border bg-popover p-1.5 shadow-xl outline-none">
          <div className="px-2.5 pb-2 pt-1.5 leading-tight">
            <div className="truncate text-sm font-medium">{user?.name}</div>
            <div className="truncate text-xs text-muted-foreground">{user?.email}</div>
            <div className="mt-1 text-[10px] uppercase tracking-wider text-muted-foreground">{plan}</div>
            {user?.email_verified === false && (
              <NavLink to="/settings" onClick={() => setOpen(false)} className="mt-1.5 flex items-center gap-1 text-[11px] text-warning hover:underline" title={t("account.unverified")}>
                <MailWarning className="size-3" /> {t("account.unverified")}
              </NavLink>
            )}
          </div>

          <div className="border-t border-border/70 py-1.5 lg:hidden">
            <QuoteStrip stack />
          </div>
          <div role="group" className="flex flex-wrap gap-1.5 border-t border-border/70 px-1 py-2" aria-label={t("market.status.label")}>
            <MarketStatusPill market="TR" tag />
            <MarketStatusPill market="US" tag />
          </div>

          <div className="border-t border-border/70 pt-1.5">
            <NavLink to="/settings" onClick={() => setOpen(false)} className={item}><Settings className="size-4 text-muted-foreground" /> {t("nav.settings")}</NavLink>
            <NavLink to="/desktop" onClick={() => setOpen(false)} className={item}><Monitor className="size-4 text-muted-foreground" /> {t("nav.desktop")}</NavLink>
          </div>

          <div className="mt-1 flex items-center gap-2 border-t border-border/70 px-2.5 pt-2">
            <span className="w-10 text-[11px] text-muted-foreground">{t("lang.label")}</span>
            <div className="flex flex-1 rounded-md border border-border bg-card p-0.5" role="radiogroup" aria-label={t("lang.label")}>
              {(["tr", "en"] as const).map((l) => (
                <button key={l} role="radio" aria-checked={lang === l} onClick={() => pickLang(l)} className={seg(lang === l)}>{t(l === "tr" ? "menu.lang.tr" : "menu.lang.en")}</button>
              ))}
            </div>
          </div>
          <div className="mt-1.5 flex items-center gap-2 px-2.5">
            <span className="w-10 text-[11px] text-muted-foreground">{t("nav.theme")}</span>
            <div className="flex flex-1 rounded-md border border-border bg-card p-0.5" role="radiogroup" aria-label={t("nav.theme")}>
              {THEMES.map(({ mode: m, key, icon: Icon }) => (
                <button key={m} role="radio" aria-checked={mode === m} onClick={() => setMode(m)} className={seg(mode === m)}><Icon className="size-3.5" aria-hidden /> <span className="sr-only sm:not-sr-only">{t(key)}</span></button>
              ))}
            </div>
          </div>

          <div className="mt-1.5 border-t border-border/70 pt-1.5">
            <button onClick={() => { setOpen(false); withOneSignal((os) => os.logout()); logout() }} className={item}><LogOut className="size-4 text-muted-foreground" /> {t("nav.logout")}</button>
          </div>
        </div>
      )}
    </div>
  )
}
