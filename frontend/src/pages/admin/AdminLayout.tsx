import { NavLink, Outlet } from "react-router-dom"
import { Database, Newspaper, Settings, ShieldCheck, Users } from "lucide-react"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"
import type { Key } from "@/i18n/tr"
import { cn } from "@/lib/utils"

type NavItem = { to: string; key: Key; icon: React.ComponentType<{ className?: string }>; end?: boolean }
const ITEMS: NavItem[] = [
  { to: "/admin", key: "admin.nav.overview", icon: Database, end: true },
  { to: "/admin/users", key: "admin.nav.users", icon: Users },
  { to: "/admin/review", key: "admin.nav.review", icon: ShieldCheck },
  { to: "/admin/news", key: "admin.nav.news", icon: Newspaper },
  { to: "/admin/settings", key: "admin.nav.settings", icon: Settings },
]

/** Admin area: side menu on wide screens, scrollable tab row on narrow ones; sub-pages render in the outlet. */
export function AdminLayout() {
  const { user } = useAuth()
  const { t } = useI18n()
  if (user?.role !== "ADMIN") return <div className="rounded-lg border border-dashed p-10 text-center text-sm text-muted-foreground">{t("admin.only")}</div>
  return (
    <div className="grid gap-5 lg:grid-cols-[210px_1fr]">
      <aside className="lg:sticky lg:top-20 lg:self-start">
        <div className="mb-2 hidden px-2 text-[11px] uppercase tracking-wider text-muted-foreground lg:block">{t("admin.title")}</div>
        <nav className="flex gap-1 overflow-x-auto rounded-lg border border-border bg-card p-1 lg:flex-col">
          {ITEMS.map(({ to, key, icon: Icon, end }) => (
            <NavLink
              key={to}
              to={to}
              end={end}
              className={({ isActive }) => cn("flex shrink-0 items-center gap-2 rounded-md px-2.5 py-1.5 text-[13px] transition", isActive ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}
            >
              <Icon className="size-4" /> {t(key)}
            </NavLink>
          ))}
        </nav>
      </aside>
      <div className="min-w-0 space-y-5"><Outlet /></div>
    </div>
  )
}
