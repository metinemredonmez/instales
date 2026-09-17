import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Portfolio, PortfolioDetail, SearchHit } from "@/lib/api"
import type { User } from "@/lib/auth"
import { LangProvider, translate } from "@/lib/i18n"
import { MarketProvider } from "@/lib/market"
import { setLocale } from "@/lib/format"

const portfolios = vi.fn<() => Promise<Portfolio[]>>()
const portfolio = vi.fn<(id: number) => Promise<PortfolioDetail>>()
const search = vi.fn<(m: string, q: string) => Promise<SearchHit[]>>()
const upsertPosition = vi.fn<(id: number, body: Record<string, unknown>) => Promise<unknown>>()
const deleteTransaction = vi.fn<(id: number, tx: number) => Promise<void>>()
vi.mock("@/lib/api", async (orig) => ({
  ...(await orig<typeof import("@/lib/api")>()),
  api: {
    portfolios: () => portfolios(),
    portfolio: (id: number) => portfolio(id),
    search: (m: string, q: string) => search(m, q),
    upsertPosition: (id: number, body: Record<string, unknown>) => upsertPosition(id, body),
    deleteTransaction: (id: number, tx: number) => deleteTransaction(id, tx),
    addTransaction: vi.fn(), deletePosition: vi.fn(), createPortfolio: vi.fn(), renamePortfolio: vi.fn(), deletePortfolio: vi.fn(),
  },
}))
let user: User | null = null
const refreshUser = vi.fn<() => Promise<void>>()
vi.mock("@/lib/auth", async (orig) => ({ ...(await orig<typeof import("@/lib/auth")>()), useAuth: () => ({ user, refreshUser }) }))

import { ApiError, PlanLimitError } from "@/lib/api"
import { PortfolioPage, positionFormError, transactionFormError } from "./PortfolioPage"

const PRO: User = { id: 7, email: "e@x.com", name: "Emre", plan: "PRO", role: "USER", plans_enforced: true, features: { portfolio: true, portfolios: 1, portfolio_positions: 100 } }
const FREE: User = { id: 8, email: "f@x.com", name: "Free", plan: "FREE", role: "USER", plans_enforced: true, features: { portfolio: false, portfolios: 0 } }
const P1: Portfolio = { id: 1, name: "Ana", market: "TR", currency: "TRY", positions: 2, created_at: "2026-09-01T00:00:00Z" }
const DETAIL: PortfolioDetail = {
  portfolio: P1,
  as_of: "2026-09-16",
  positions: [
    { id: 11, symbol: "ASELS", name: "Aselsan", derived: false, quantity: 1000, avg_cost: 50, opened_at: null, note: null, last_close: 62.5, close_date: "2026-09-16", market_value: 62500, cost_value: 50000, pnl_value: 12500, pnl_pct: 25, weight_pct: 41.7, smart_money_score: 78, consensus_score: 64, crowding_score: 40, funds_increasing_30d: 4, funds_reducing_30d: 1, insiders_net_90d: 4_400_000 },
    { id: 12, symbol: "THYAO", name: "Türk Hava Yolları", derived: false, quantity: 300, avg_cost: 300, opened_at: null, note: null, last_close: 291, close_date: "2026-09-16", market_value: 87300, cost_value: 90000, pnl_value: -2700, pnl_pct: -3, weight_pct: 58.3, smart_money_score: 45, consensus_score: null, crowding_score: null, funds_increasing_30d: 2, funds_reducing_30d: 3, insiders_net_90d: null },
  ],
  totals: { market_value: 149800, cost_value: 140000, pnl_value: 9800, pnl_pct: 7, unpriced: 0 },
  moves: {
    window_days: 30, window_start: "2026-08-17",
    rows: [{ symbol: "ASELS", name: "Aselsan", net_flow_value: 1500000, net_qty: 12000, funds_increasing: 4, funds_reducing: 1, funds_new: 1, funds_exited: 0, party_count: 1, parties: [{ kind: "fund", code: "TMV", name: "TMV Fonu", activity: "ADD", delta_qty: 12000, delta_value: 1500000, to_weight_pct: 3.1, delta_weight_pct: 0.4, period_end: "2026-09-15", confidence: "EXACT" }] }],
  },
  transactions: [],
}

function setup() {
  localStorage.setItem("instilens.market", "TR")
  localStorage.setItem("instilens.lang", "tr")
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <LangProvider>
        <MarketProvider>
          <MemoryRouter><PortfolioPage /></MemoryRouter>
        </MarketProvider>
      </LangProvider>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

describe("PortfolioPage", () => {
  beforeEach(() => {
    portfolios.mockReset(); portfolio.mockReset(); search.mockReset(); upsertPosition.mockReset(); deleteTransaction.mockReset(); refreshUser.mockReset()
    refreshUser.mockResolvedValue(undefined)
    portfolios.mockResolvedValue([P1])
    portfolio.mockResolvedValue(DETAIL)
    search.mockResolvedValue([{ kind: "stock", key: "TR:ASELS", label: "ASELS", name: "Aselsan", href: "/stocks/ASELS" }])
    upsertPosition.mockResolvedValue({})
    user = PRO
  })
  afterEach(() => { localStorage.removeItem("instilens.market"); localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("renders the positions, the totals and the moves of the picked portfolio, P&L coloured by sign only", async () => {
    setup()
    // "ASELS" links twice — the position row and its move; scope to the positions section.
    const positions = () => within(screen.getByText("Pozisyonlar").closest("section")!)
    await screen.findByText("Pozisyonlar")
    expect(await positions().findByRole("link", { name: "ASELS" })).toHaveAttribute("href", "/stocks/ASELS")
    expect(portfolio).toHaveBeenCalledWith(1)
    const row = (sym: string) => positions().getByRole("link", { name: sym }).closest("tr")!
    // ASELS: 1000 lots, +₺12.5K (+25 %) in green; THYAO: -₺2.7K in red.
    expect(within(row("ASELS")).getByText("1.000")).toBeInTheDocument()
    const up = within(row("ASELS")).getByText("+₺12.5K").closest("td")!
    expect(up.className).toContain("text-positive")
    expect(within(up).getByText("+25,0%")).toBeInTheDocument()
    const down = within(row("THYAO")).getByText("-₺2.7K").closest("td")!
    expect(down.className).toContain("text-negative")
    expect(down.className).not.toContain("text-positive")
    // Totals tiles: levels unsigned, P&L signed.
    expect(screen.getByText("Piyasa değeri", { selector: "div" }).nextElementSibling!.textContent).toBe("₺150 bin")
    expect(screen.getByText("K/Z", { selector: "div" }).nextElementSibling!.textContent).toBe("+₺9.8K")
    expect(screen.getByText("K/Z %").nextElementSibling!.textContent).toBe("+7,0%")
    // Moves: descriptive line and the fund behind it, never advisory.
    expect(screen.getByText("4 fon artırdı · 1 fon azalttı · 1 yeni")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: /TMV Fonu.*ADD/ })).toHaveAttribute("href", "/funds/TMV")
    expect(screen.queryByText(/Pro ile açılır/)).toBeNull()
    // The positions table carries its own reading rule, as the insiders and fundamentals sections do.
    expect(positions().getByText("son kapanış fiyatıyla · tavsiye değildir")).toBeInTheDocument()
    // The insiders' 90-day net value is a column on BIST too (KAP filings), in lira; "—" where the feed has nothing yet.
    expect(positions().getByRole("columnheader", { name: "İçeriden net 90G" })).toBeInTheDocument()
    expect(within(row("ASELS")).getByText("+₺4.4M")).toBeInTheDocument()
    expect(within(row("THYAO")).getAllByRole("cell").at(-2)!.textContent).toBe("—")
  })

  it("a refused removal (400: the buy's later sale would oversell) is printed, not swallowed", async () => {
    portfolio.mockResolvedValue({ ...DETAIL, transactions: [{ id: 5, symbol: "ASELS", side: "BUY", quantity: 100, price: 50, traded_at: "2026-08-01", fee: null, note: null }] })
    deleteTransaction.mockRejectedValue(new ApiError(400, "sale of 100 on 2026-08-09 exceeds the quantity held", "400 sale of 100 on 2026-08-09 exceeds the quantity held"))
    const u = setup()
    await screen.findByText("İşlemler")
    await u.click(screen.getByRole("button", { name: "Sil" }))
    await waitFor(() => expect(deleteTransaction).toHaveBeenCalledWith(1, 5))
    expect((await screen.findByRole("alert")).textContent).toMatch(/exceeds the quantity held/)
  })

  it("shows the calm plan-locked card when the list answers 402 plan_limit", async () => {
    portfolios.mockRejectedValue(new PlanLimitError({ detail: "plan_limit", feature: "portfolio", plan: "FREE", limit: 0, upgrade: "PRO" }))
    setup()
    const card = await screen.findByRole("status")
    expect(within(card).getByText("Portföy takibi Pro ile açılır.")).toBeInTheDocument()
    expect(within(card).getByText("Pro ile açılır")).toBeInTheDocument()
    expect(within(card).getByRole("link", { name: "Planları gör" })).toHaveAttribute("href", "/plan")
    expect(portfolio).not.toHaveBeenCalled()
    expect(screen.queryByRole("button", { name: /Pozisyon ekle/ })).toBeNull()
  })

  it("locks from the account's own feature matrix without asking the API — but only while plans are enforced", async () => {
    user = FREE
    setup()
    expect(await screen.findByRole("link", { name: "Planları gör" })).toBeInTheDocument()
    expect(portfolios).not.toHaveBeenCalled()
    // The matrix is the session's copy: a client-side lock re-reads the account once, so an upgrade needs no reload.
    await waitFor(() => expect(refreshUser).toHaveBeenCalledTimes(1))
  })

  it("with the runtime switch off a FREE matrix locks nothing client-side: the page asks and the API decides", async () => {
    user = { ...FREE, plans_enforced: false }
    setup()
    expect(await screen.findByText("Pozisyonlar")).toBeInTheDocument()
    expect(portfolios).toHaveBeenCalled()
  })

  it("add-position dialog refuses a zero quantity or a non-positive cost without sending, then sends the parsed numbers", async () => {
    const user_ = setup()
    await user_.click(await screen.findByRole("button", { name: "Pozisyon ekle" }))
    const dialog = await screen.findByRole("dialog")
    const [qty, cost] = within(dialog).getAllByRole("spinbutton")
    const submit = within(dialog).getByRole("button", { name: "Ekle" })
    await user_.type(within(dialog).getByRole("combobox"), "ASELS")
    await user_.type(qty, "0")
    await user_.click(submit)
    expect(within(dialog).getByRole("alert").textContent).toBe("Adet 0'dan büyük olmalı.")
    expect(upsertPosition).not.toHaveBeenCalled()
    await user_.clear(qty)
    await user_.type(qty, "100")
    await user_.type(cost, "-5")
    await user_.click(submit)
    expect(within(dialog).getByRole("alert").textContent).toBe("Fiyat 0'dan büyük olmalı.")
    expect(upsertPosition).not.toHaveBeenCalled()
    await user_.clear(cost)
    await user_.type(cost, "50.5")
    await user_.click(submit)
    await waitFor(() => expect(upsertPosition).toHaveBeenCalledWith(1, { symbol: "ASELS", quantity: 100, avg_cost: 50.5, opened_at: null, note: null }))
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull())
  })

  it("a 402 on the add itself shows the plan message inside the dialog", async () => {
    upsertPosition.mockRejectedValue(new PlanLimitError({ detail: "plan_limit", feature: "portfolio_positions", plan: "PRO", limit: 100, upgrade: "PRO_PLUS" }))
    const user_ = setup()
    await user_.click(await screen.findByRole("button", { name: "Pozisyon ekle" }))
    const dialog = await screen.findByRole("dialog")
    await user_.type(within(dialog).getByRole("combobox"), "GARAN")
    await user_.type(within(dialog).getAllByRole("spinbutton")[0], "10")
    await user_.click(within(dialog).getByRole("button", { name: "Ekle" }))
    expect((await within(dialog).findByRole("alert")).textContent).toBe("Portföy başına pozisyon limitine ulaşıldı (100) — Pro+ ile artar.")
  })
})

describe("positionFormError / transactionFormError", () => {
  const t = (k: Parameters<typeof translate>[1], v?: Parameters<typeof translate>[2]) => translate("en", k, v)
  it("needs a symbol, a positive quantity and — when typed — a positive cost; a decimal comma is fine", () => {
    expect(positionFormError(t, { symbol: "", quantity: "1", avg_cost: "" })).toBe("Pick a stock.")
    expect(positionFormError(t, { symbol: "ASELS", quantity: "", avg_cost: "" })).toBe("Quantity must be greater than 0.")
    expect(positionFormError(t, { symbol: "ASELS", quantity: "-1", avg_cost: "" })).toBe("Quantity must be greater than 0.")
    expect(positionFormError(t, { symbol: "ASELS", quantity: "abc", avg_cost: "" })).toBe("Quantity must be greater than 0.")
    expect(positionFormError(t, { symbol: "ASELS", quantity: "10", avg_cost: "0" })).toBe("Price must be greater than 0.")
    expect(positionFormError(t, { symbol: "ASELS", quantity: "10", avg_cost: "" })).toBeNull()
    expect(positionFormError(t, { symbol: "ASELS", quantity: "10,5", avg_cost: "12,25" })).toBeNull()
  })
  it("a transaction also needs a positive price and a date", () => {
    expect(transactionFormError(t, { symbol: "NVDA", quantity: "10", price: "", traded_at: "2026-09-16" })).toBe("Price must be greater than 0.")
    expect(transactionFormError(t, { symbol: "NVDA", quantity: "10", price: "0", traded_at: "2026-09-16" })).toBe("Price must be greater than 0.")
    expect(transactionFormError(t, { symbol: "NVDA", quantity: "10", price: "150", traded_at: "" })).toBe("Trade date is required.")
    expect(transactionFormError(t, { symbol: "NVDA", quantity: "10", price: "150", traded_at: "2026-09-16" })).toBeNull()
  })
})
