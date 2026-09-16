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
import { AdminPage } from "@/pages/AdminPage"
import { useAuth } from "@/lib/auth"

export default function App() {
  const { user } = useAuth()
  if (!user) return <LoginPage />
  return (
    <AppShell>
      <Routes>
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
        <Route path="/admin" element={<AdminPage />} />
      </Routes>
    </AppShell>
  )
}
