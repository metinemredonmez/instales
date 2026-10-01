import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import { PortfolioAnalysis } from "./PortfolioAnalysis"
import type { Portfolio, PortfolioAnalysis as Analysis } from "@/lib/api"
import { LangProvider } from "@/lib/i18n"
import { MarketProvider } from "@/lib/market"

const { analyze } = vi.hoisted(() => ({ analyze: vi.fn<(id: number) => Promise<Analysis>>() }))
vi.mock("@/lib/api", () => ({ api: { portfolioAnalysis: analyze } }))
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 } }) }))

const PORTFOLIO: Portfolio = { id: 7, name: "US", market: "US", currency: "USD", created_at: "2026-09-01" }
const DATA: Analysis = {
  as_of: "2026-09-16", lookback_days: 366,
  concentration: { priced_positions: 2, total_positions: 2, missing_symbols: [], stale_symbols: [], largest_weight_pct: 60, top3_weight_pct: 100, hhi: 5200, allocations: [{ symbol: "AAPL", weight_pct: 60 }, { symbol: "MSFT", weight_pct: 40 }] },
  risk: { status: "ready", start: "2026-08-01", end: "2026-09-16", observations: 30, min_returns: 21, missing_dates: 0, invalid_symbols: [], price_change_pct: 4, annualized_volatility_pct: 0, max_drawdown_pct: 0 },
  price_sources: [{ position_id: 1, instrument_id: 10, symbol: "AAPL", latest_date: "2026-09-16", providers: ["yahoo"], observations: 30 }],
  common_funds: { total: 2, rows: [
    { code: "CIK1", name: "Fund One", as_of: "2026-06-30", snapshot_id: 10, disclosure_id: 40, confidence: "EXACT", symbols: ["AAPL", "MSFT"] },
    { code: "CIK2", name: "Fund Two", as_of: "2026-06-30", snapshot_id: 11, disclosure_id: 41, confidence: "EXACT", symbols: ["AAPL", "MSFT"] },
  ] },
}

function setup(lang = "tr") {
  localStorage.setItem("instilens.lang", lang)
  localStorage.setItem("instilens.market", "TR")
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={client}><LangProvider><MarketProvider><MemoryRouter><PortfolioAnalysis portfolio={PORTFOLIO} /></MemoryRouter></MarketProvider></LangProvider></QueryClientProvider>)
  return userEvent.setup()
}

describe("PortfolioAnalysis", () => {
  beforeEach(() => { analyze.mockReset(); analyze.mockResolvedValue(structuredClone(DATA)) })
  afterEach(() => { localStorage.clear() })

  it("shows real zero risk distinctly from missing data, methodology and disclosure lineage", async () => {
    const user = setup()
    await screen.findByText("Yoğunlaşma (HHI)")
    expect(analyze).toHaveBeenCalledWith(7)
    expect(screen.getByText("Yıllık oynaklık").nextElementSibling).toHaveTextContent("0,00%")
    expect(screen.getByText("En büyük düşüş").nextElementSibling).toHaveTextContent("0,00%")
    expect(screen.getByText(/Bugünkü adetler geçmişte sabit/)).toBeInTheDocument()
    expect(screen.getByText(/Bildirim #40/)).toBeInTheDocument()
    expect(screen.getAllByText("EXACT")).toHaveLength(2)
    await user.click(screen.getByText("Fiyat kaynakları ve veri tarihleri"))
    expect(screen.getByText(/AAPL · yahoo/)).toBeVisible()
    const compare = screen.getByRole("link", { name: /Bu fonların ortak hisselerini/ })
    expect(compare).toHaveAttribute("href", "/compare?codes=CIK1%2CCIK2")
    await user.click(compare)
    expect(localStorage.getItem("instilens.market")).toBe("US")
  })

  it("labels partial coverage and withholds missing metrics in English", async () => {
    const data = structuredClone(DATA)
    data.concentration.priced_positions = 1
    data.concentration.missing_symbols = ["MSFT"]
    data.risk = { ...data.risk, status: "missing_prices", price_change_pct: null, annualized_volatility_pct: null, max_drawdown_pct: null }
    analyze.mockResolvedValue(data)
    setup("en")
    expect(await screen.findByText(/1 of 2 positions priced/)).toBeInTheDocument()
    expect(screen.getByText(/Missing prices: MSFT/)).toBeInTheDocument()
    expect(screen.getByRole("status")).toHaveTextContent("Historical risk requires prices for every position.")
    expect(screen.getByText("Annual volatility").nextElementSibling).toHaveTextContent("—")
  })

  it("shows a retryable request error instead of an empty fund result", async () => {
    analyze.mockRejectedValueOnce(new Error("unavailable"))
    const user = setup()
    const error = await screen.findByRole("alert")
    expect(error).toHaveTextContent("Portföy analizi yüklenemedi.")
    expect(screen.queryByText(/birlikte tutan fon bulunamadı/)).toBeNull()
    await user.click(within(error).getByRole("button", { name: "Yeniden dene" }))
    expect(await screen.findByText("Yoğunlaşma (HHI)")).toBeInTheDocument()
  })
})
