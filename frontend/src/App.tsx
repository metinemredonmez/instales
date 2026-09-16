import { Route, Routes } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { RadarPage } from "@/pages/RadarPage"
import { StockPage } from "@/pages/StockPage"
import { FundPage } from "@/pages/FundPage"
import { LivePage } from "@/pages/LivePage"
import { ScreenerPage } from "@/pages/ScreenerPage"
import { ResearchPage } from "@/pages/ResearchPage"
import { LoginPage } from "@/pages/LoginPage"
import { WatchlistPage } from "@/pages/WatchlistPage"
import { AlertsPage } from "@/pages/AlertsPage"
import { InstitutionPage, InstitutionsPage } from "@/pages/InstitutionsPage"
import { ComparePage } from "@/pages/ComparePage"
import { AdminLayout } from "@/pages/admin/AdminLayout"
import { AdminOverviewPage } from "@/pages/admin/AdminOverviewPage"
import { AdminUsersPage } from "@/pages/admin/AdminUsersPage"
import { AdminReviewPage } from "@/pages/admin/AdminReviewPage"
import { AdminNewsPage } from "@/pages/admin/AdminNewsPage"
import { AdminSettingsPage } from "@/pages/admin/AdminSettingsPage"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"

export default function App() {
  const { user } = useAuth()
  const { lang } = useI18n()
  if (!user) return <LoginPage />
  return (
    <AppShell>
      {/* key: remount pages when the language changes so memoised labels/formatters pick up the new locale */}
      <Routes key={lang}>
        <Route path="/" element={<RadarPage />} />
        <Route path="/stocks/:symbol" element={<StockPage />} />
        <Route path="/funds/:code" element={<FundPage />} />
        <Route path="/live" element={<LivePage />} />
        <Route path="/screener" element={<ScreenerPage />} />
        <Route path="/research" element={<ResearchPage />} />
        <Route path="/watchlist" element={<WatchlistPage />} />
        <Route path="/alerts" element={<AlertsPage />} />
        <Route path="/institutions" element={<InstitutionsPage />} />
        <Route path="/institutions/:code" element={<InstitutionPage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<AdminOverviewPage />} />
          <Route path="users" element={<AdminUsersPage />} />
          <Route path="review" element={<AdminReviewPage />} />
          <Route path="news" element={<AdminNewsPage />} />
          <Route path="settings" element={<AdminSettingsPage />} />
        </Route>
      </Routes>
    </AppShell>
  )
}
