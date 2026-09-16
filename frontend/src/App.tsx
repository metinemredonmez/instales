import { Route, Routes, useLocation } from "react-router-dom"
import { AppShell } from "@/components/layout/AppShell"
import { NowSpeaking } from "@/components/domain/NowSpeaking"
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
import { AdminReleasesPage } from "@/pages/admin/AdminReleasesPage"
import { DesktopPage } from "@/pages/DesktopPage"
import { SettingsPage } from "@/pages/SettingsPage"
import { ResetPasswordPage } from "@/pages/ResetPasswordPage"
import { VerifyEmailPage } from "@/pages/VerifyEmailPage"
import { NotFoundPage } from "@/pages/NotFoundPage"
import { PublicFrame } from "@/components/layout/PublicFrame"
import { useAuth } from "@/lib/auth"
import { useI18n } from "@/lib/i18n"

// Reachable without a session: e-mail links (reset / verify) and the download page, which reads a public endpoint.
const PUBLIC = ["/reset", "/verify"]

export default function App() {
  const { user } = useAuth()
  const { lang } = useI18n()
  const { pathname } = useLocation()
  if (PUBLIC.includes(pathname.replace(/\/+$/, "") || "/")) {
    return (
      <Routes key={lang}>
        <Route path="/reset" element={<ResetPasswordPage />} />
        <Route path="/verify" element={<VerifyEmailPage />} />
      </Routes>
    )
  }
  if (!user) {
    return (
      <Routes key={lang}>
        <Route path="/desktop" element={<PublicFrame wide><DesktopPage /></PublicFrame>} />
        <Route path="*" element={<LoginPage />} />
      </Routes>
    )
  }
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
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="/institutions" element={<InstitutionsPage />} />
        <Route path="/institutions/:code" element={<InstitutionPage />} />
        <Route path="/compare" element={<ComparePage />} />
        <Route path="/desktop" element={<DesktopPage />} />
        <Route path="/admin" element={<AdminLayout />}>
          <Route index element={<AdminOverviewPage />} />
          <Route path="users" element={<AdminUsersPage />} />
          <Route path="review" element={<AdminReviewPage />} />
          <Route path="news" element={<AdminNewsPage />} />
          <Route path="settings" element={<AdminSettingsPage />} />
          <Route path="releases" element={<AdminReleasesPage />} />
        </Route>
        <Route path="*" element={<NotFoundPage />} />
      </Routes>
      {/* Narration widget: global so a note keeps reading while the user moves between pages */}
      <NowSpeaking />
    </AppShell>
  )
}
