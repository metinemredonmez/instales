import { Activity, ArrowLeftRight, Bell, Building2, GitCompare, Monitor, Radar, Settings, Shield, SlidersHorizontal, Sparkles, Star } from "lucide-react"
import type { Key } from "@/i18n/tr"

export type NavItem = { to: string; key: Key; icon: React.ComponentType<{ className?: string }>; end?: boolean; mobile?: boolean }

/** Market pages (sidebar group "Piyasa"); `mobile` marks the ones on the phone bottom bar. */
export const NAV: NavItem[] = [
  { to: "/", key: "nav.radar", icon: Radar, end: true, mobile: true },
  { to: "/live", key: "nav.live", icon: Activity, mobile: true },
  // Not on the phone bar (full at five + admin, like /institutions and /compare); phones reach it from a fund page's "Moves" link and ⌘K.
  { to: "/moves", key: "nav.moves", icon: ArrowLeftRight },
  { to: "/screener", key: "nav.screener", icon: SlidersHorizontal, mobile: true },
  { to: "/institutions", key: "nav.institutions", icon: Building2 },
  { to: "/compare", key: "nav.compare", icon: GitCompare },
  { to: "/research", key: "nav.research", icon: Sparkles, mobile: true },
]
/** Personal pages (sidebar group "Kişisel"). */
export const MINE: NavItem[] = [
  { to: "/watchlist", key: "nav.watchlist", icon: Star, mobile: true },
  { to: "/alerts", key: "alerts.title", icon: Bell },
  { to: "/settings", key: "nav.settings", icon: Settings },
  { to: "/desktop", key: "nav.desktop", icon: Monitor },
]
export const ADMIN: NavItem = { to: "/admin", key: "nav.admin", icon: Shield }
