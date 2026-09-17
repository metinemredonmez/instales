import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Fundamentals as FundamentalsData, FundamentalsPeriod, FundamentalsSummary, Statement } from "@/lib/api"
import { LangProvider } from "@/lib/i18n"
import { setLocale } from "@/lib/format"

const fundamentals = vi.fn<(market: string, symbol: string, period: FundamentalsPeriod) => Promise<FundamentalsData>>()
vi.mock("@/lib/api", () => ({ api: { fundamentals: (m: string, s: string, p: FundamentalsPeriod) => fundamentals(m, s, p) } }))

import { Fundamentals, FundamentalsChips } from "./Fundamentals"

const income = (year: number, revenue: number | null, net: number | null): Statement => ({
  period_end: `${year}-12-31`,
  items: { revenue, cost_of_revenue: null, gross_profit: null, operating_income: null, ebitda: null, pretax_income: null, net_income: net, eps_diluted: net === null ? null : 12.345, interest_expense: null },
})

/** Six annual income statements (one more than the table shows), a balance sheet, no cash flow — Yahoo often lacks one. */
const DATA: FundamentalsData = {
  symbol: "ASELS", name: "Aselsan", market: "TR", currency: "TRY", source: "yahoo", fetched_at: "2026-09-16T14:05:00Z",
  snapshot: {
    as_of: "2026-09-16", quote_currency: "TRY", market_cap: 1_200_000_000, enterprise_value: 1_450_000_000, pe: 12.34, forward_pe: null, price_to_book: null, price_to_sales: 2.1,
    ev_to_ebitda: 8.76, profit_margin: 18.44, operating_margin: 21.0, return_on_assets: 6.1, return_on_equity: 24.9, revenue_ttm: 900_000_000, ebitda_ttm: null,
    net_income_ttm: 166_000_000, eps_ttm: 3.1, dividend_yield: 1.25, payout_ratio: null, beta: 0.954, week52_high: 78.9, week52_low: 45.2,
    shares_outstanding: 4_560_000_000, float_shares: null, short_percent_of_float: null,
  },
  period: "annual",
  statements: {
    income: [income(2025, 345_000_000, -50_000_000), income(2024, 300_000_000, 40_000_000), income(2023, 250_000_000, null), income(2022, 200_000_000, 20_000_000), income(2021, 150_000_000, 10_000_000), income(2020, 100_000_000, 5_000_000)],
    balance: [{ period_end: "2025-12-31", items: { total_assets: 2_000_000_000, total_liabilities: 1_200_000_000, equity: 800_000_000, total_debt: null, cash: 150_000_000, current_assets: null, current_liabilities: null } }],
    cashflow: [],
  },
  derived: { gross_margin: null, operating_margin: 21.0, net_margin: -14.5, fcf_margin: null, debt_to_equity: 85.0, revenue_growth_yoy: 15.0, net_income_growth_yoy: -225.0, period_end: "2025-12-31" },
}

const EMPTY: FundamentalsData = {
  ...DATA, fetched_at: null, snapshot: null, statements: { income: [], balance: [], cashflow: [] },
  derived: { gross_margin: null, operating_margin: null, net_margin: null, fcf_margin: null, debt_to_equity: null, revenue_growth_yoy: null, net_income_growth_yoy: null, period_end: null },
}

/** A cash flow statement as Yahoo prints it: capex, dividends and buybacks are negative outflows on every filer. */
const CASHFLOW: Statement = { period_end: "2025-12-31", items: { operating_cf: 300_000_000, capex: -100_000_000, free_cf: -20_000_000, dividends_paid: -50_000_000, share_repurchase: null } }

const SUMMARY: FundamentalsSummary = {
  as_of: "2026-09-16", market_cap: 386_983_919_616, pe: null, price_to_book: 1.9, net_margin: 10.2, revenue_growth_yoy: 4.0, dividend_yield: 2.28,
  shares_outstanding: 1_372_283_353, currency: "USD", quote_currency: "TRY", source: "yahoo",
}

function setup(lang: "tr" | "en" = "tr", ui: React.ReactNode = <Fundamentals symbol="ASELS" market="TR" />) {
  localStorage.setItem("instilens.lang", lang)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={qc}>
      <LangProvider>{ui}</LangProvider>
    </QueryClientProvider>,
  )
  return { user: userEvent.setup(), ...utils }
}

describe("Fundamentals", () => {
  beforeEach(() => { fundamentals.mockReset(); fundamentals.mockResolvedValue(DATA) })
  afterEach(() => { localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("renders the valuation grid with compact Turkish money and dashes for missing figures", async () => {
    const { container } = setup()
    expect(await screen.findByText("Temel veriler")).toBeInTheDocument()
    expect(fundamentals).toHaveBeenLastCalledWith("TR", "ASELS", "annual")
    const grid = within(container.querySelector("dl")!)
    const metric = (label: string) => grid.getByText(label).nextElementSibling?.textContent
    expect(metric("Piyasa değeri")).toBe("₺1,2 mr")
    expect(metric("Firma değeri")).toBe("₺1,5 mr")
    expect(metric("F/K")).toBe("12,3")
    expect(metric("İleri F/K (tahmin)")).toBe("—")           // the one estimate-based figure says so
    expect(metric("PD/DD")).toBe("—")
    expect(metric("FD/FAVÖK")).toBe("8,8")
    expect(metric("Net kâr marjı (son 12 ay)")).toBe("18,4%")  // Yahoo's TTM margin, not the derived annual one
    expect(metric("Özkaynak kârlılığı")).toBe("24,9%")
    expect(metric("Temettü verimi")).toBe("1,3%")
    expect(metric("Beta")).toBe("0,95")
    expect(metric("52 hafta aralığı")).toBe("₺45,20 – ₺78,90")
    expect(metric("Toplam pay sayısı")).toBe("4,6 mr")       // a count, no currency
    expect(screen.getByText("Değerleme · 16 Eyl 2026")).toBeInTheDocument()
  })

  it("prices market cap, EV and the 52-week range in the listing currency and the statements in the reporting one", async () => {
    // THYAO's shape: files in USD, trades in TRY. A four-digit share price is a price, never "₺1,2 bin".
    fundamentals.mockResolvedValue({ ...DATA, currency: "USD", snapshot: { ...DATA.snapshot!, quote_currency: "TRY", market_cap: 386_983_919_616, week52_low: 45.2, week52_high: 1234.5 } })
    const { container } = setup()
    expect(await screen.findByText("Temel veriler")).toBeInTheDocument()
    const grid = within(container.querySelector("dl")!)
    const metric = (label: string) => grid.getByText(label).nextElementSibling?.textContent
    expect(metric("Piyasa değeri")).toBe("₺387 mr")
    expect(metric("Firma değeri")).toBe("₺1,5 mr")
    expect(metric("52 hafta aralığı")).toBe("₺45,20 – ₺1.234,50")
    const heads = within(screen.getByRole("table")).getAllByRole("columnheader").map((h) => h.textContent)
    expect(heads[0]).toBe("USD")
    expect(within(screen.getByRole("table")).getByText("Hasılat").closest("tr")!.textContent).toContain("$345 mn")
  })

  it("shows the newest five periods of the income statement, negatives in red and EPS with its decimals", async () => {
    setup()
    const table = await screen.findByRole("table")
    const heads = within(table).getAllByRole("columnheader").map((h) => h.textContent)
    expect(heads).toEqual(["TRY", "12/2025", "12/2024", "12/2023", "12/2022", "12/2021"])   // 2020 is the sixth period: cut
    const row = (label: string) => within(within(table).getByText(label).closest("tr")!).getAllByRole("cell").slice(1).map((c) => c.textContent)
    expect(row("Hasılat")).toEqual(["₺345 mn", "₺300 mn", "₺250 mn", "₺200 mn", "₺150 mn"])
    expect(row("Net kâr")).toEqual(["-₺50,0 mn", "₺40,0 mn", "—", "₺20,0 mn", "₺10,0 mn"])
    expect(row("Seyreltilmiş HBK")).toEqual(["12,35", "12,35", "—", "12,35", "12,35"])
    expect(row("Satışların maliyeti")).toEqual(["—", "—", "—", "—", "—"])
    const loss = within(table).getByText("-₺50,0 mn")
    expect(loss).toHaveClass("text-negative")
    expect(within(table).getByText("₺40,0 mn")).not.toHaveClass("text-negative")
    expect(table.parentElement).toHaveClass("overflow-x-auto")
  })

  it("paints red only where the sign means something: a cash burn yes, capex and dividends paid no", async () => {
    fundamentals.mockResolvedValue({ ...DATA, statements: { ...DATA.statements, cashflow: [CASHFLOW] } })
    const { user } = setup()
    await screen.findByRole("table")
    await user.click(screen.getByRole("button", { name: "Nakit akışı" }))
    const table = screen.getByRole("table")
    const cell = (label: string) => within(within(table).getByText(label).closest("tr")!).getAllByRole("cell")[1]
    expect(cell("Yatırım harcaması").textContent).toBe("-₺100 mn")
    expect(cell("Yatırım harcaması")).not.toHaveClass("text-negative")
    expect(cell("Ödenen temettü")).not.toHaveClass("text-negative")
    expect(cell("Serbest nakit akışı").textContent).toBe("-₺20,0 mn")
    expect(cell("Serbest nakit akışı")).toHaveClass("text-negative")
    expect(cell("İşletme nakit akışı")).not.toHaveClass("text-negative")
  })

  it("switches between the three statements; a statement Yahoo did not deliver says so", async () => {
    const { user } = setup()
    await screen.findByRole("table")
    await user.click(screen.getByRole("button", { name: "Bilanço" }))
    expect(screen.getByRole("button", { name: "Bilanço" })).toHaveAttribute("aria-pressed", "true")
    const table = screen.getByRole("table")
    expect(within(table).getByText("Toplam varlıklar").closest("tr")!.textContent).toContain("₺2,0 mr")
    expect(within(table).getByText("Toplam borç").closest("tr")!.textContent).toContain("—")
    expect(within(table).queryByText("Hasılat")).toBeNull()
    await user.click(screen.getByRole("button", { name: "Nakit akışı" }))
    expect(screen.queryByRole("table")).toBeNull()
    expect(screen.getByText("Sağlayıcıda bu dönem için bu tablo yok.")).toBeInTheDocument()
  })

  it("the annual/quarterly toggle changes the query arguments", async () => {
    const { user } = setup()
    await screen.findByRole("table")
    expect(screen.getByRole("button", { name: "Yıllık" })).toHaveAttribute("aria-pressed", "true")
    await user.click(screen.getByRole("button", { name: "Çeyreklik" }))
    await waitFor(() => expect(fundamentals).toHaveBeenLastCalledWith("TR", "ASELS", "quarterly"))
    expect(screen.getByRole("button", { name: "Çeyreklik" })).toHaveAttribute("aria-pressed", "true")
    await user.click(screen.getByRole("button", { name: "Yıllık" }))
    await waitFor(() => expect(fundamentals).toHaveBeenLastCalledWith("TR", "ASELS", "annual"))
  })

  it("keeps the last loaded period on screen when the toggled one fails, and says so", async () => {
    const { user } = setup()
    await screen.findByRole("table")
    fundamentals.mockRejectedValueOnce(new Error("502"))
    await user.click(screen.getByRole("button", { name: "Çeyreklik" }))
    expect(await screen.findByText("Bu dönem yüklenemedi.")).toBeInTheDocument()
    expect(screen.getByRole("table")).toBeInTheDocument()                        // the annual table, still there
    expect(within(screen.getByRole("table")).getByText("Hasılat")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Yıllık" })).toBeInTheDocument()   // and the way back
    await user.click(screen.getByRole("button", { name: "Yıllık" }))
    await waitFor(() => expect(screen.queryByText("Bu dönem yüklenemedi.")).toBeNull())
  })

  it("a period with a snapshot but no statements shows the statement picker with its 'none' message", async () => {
    fundamentals.mockResolvedValueOnce(DATA).mockResolvedValueOnce({ ...DATA, period: "quarterly", statements: { income: [], balance: [], cashflow: [] } })
    const { user } = setup()
    await screen.findByRole("table")
    await user.click(screen.getByRole("button", { name: "Çeyreklik" }))
    await waitFor(() => expect(screen.queryByRole("table")).toBeNull())
    expect(screen.getByRole("button", { name: "Gelir tablosu" })).toBeInTheDocument()
    expect(screen.getByText("Sağlayıcıda bu dönem için bu tablo yok.")).toBeInTheDocument()
    expect(screen.getByText("Piyasa değeri")).toBeInTheDocument()
  })

  it("colours the derived margins by sign, signs only the growth chips; leverage stays neutral", async () => {
    setup()
    await screen.findByRole("table")
    const chip = (label: string) => screen.getByText(label).nextElementSibling!
    expect(chip("Faaliyet marjı").textContent).toBe("21,0%")      // a level: no "+"
    expect(chip("Faaliyet marjı")).toHaveClass("text-positive")
    expect(chip("Net marj").textContent).toBe("-14,5%")
    expect(chip("Net marj")).toHaveClass("text-negative")
    expect(chip("Net kâr y/y").textContent).toBe("-225,0%")
    expect(chip("Hasılat y/y").textContent).toBe("+15,0%")         // a change: signed
    expect(chip("Brüt marj").textContent).toBe("—")
    expect(chip("Borç/özkaynak").textContent).toBe("85,0%")
    expect(chip("Borç/özkaynak")).not.toHaveClass("text-positive")
    expect(screen.getByText("Türetilmiş oranlar · 12/2025 dönemi")).toBeInTheDocument()
  })

  it("names the source, the fetch time and the as-reported disclaimer", async () => {
    setup()
    await screen.findByRole("table")
    expect(screen.getByText(/^Kaynak: Yahoo Finance · 16 Eyl \d{2}:\d{2} · rakamlar raporlandığı gibi, tavsiye değildir$/)).toBeInTheDocument()
  })

  it("renders English labels and English compact money after a language switch", async () => {
    const { container } = setup("en")
    expect(await screen.findByText("Fundamentals")).toBeInTheDocument()
    const grid = within(container.querySelector("dl")!)   // "Net margin" is also a derived chip in English
    const metric = (label: string) => grid.getByText(label).nextElementSibling?.textContent
    expect(metric("Market cap")).toBe("₺1.2 bn")
    expect(metric("P/E")).toBe("12.3")
    expect(metric("P/B")).toBe("—")
    expect(metric("Net margin (TTM)")).toBe("18.4%")
    expect(metric("Shares outstanding")).toBe("4.6 bn")
    expect(screen.getByRole("button", { name: "Quarterly" })).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Balance sheet" })).toBeInTheDocument()
    expect(within(screen.getByRole("table")).getByText("Revenue").closest("tr")!.textContent).toContain("₺345 mn")
    expect(screen.getByText(/^Source: Yahoo Finance · .* · figures as reported, not advice$/)).toBeInTheDocument()
  })

  it("shows the empty state, without the period toggle, when nothing has been fetched yet", async () => {
    fundamentals.mockResolvedValue(EMPTY)
    setup()
    expect(await screen.findByText("Bu hisse için temel veriler henüz çekilmedi.")).toBeInTheDocument()
    expect(screen.queryByRole("button", { name: "Yıllık" })).toBeNull()
    expect(screen.queryByText("Piyasa değeri")).toBeNull()
    expect(screen.queryByText(/Kaynak:/)).toBeNull()
  })

  it("hides itself on an API error instead of rendering a broken box", async () => {
    fundamentals.mockRejectedValue(new Error("404 unknown symbol"))
    const { container } = setup()
    await waitFor(() => expect(fundamentals).toHaveBeenCalled())
    await waitFor(() => expect(container.querySelector('[data-slot="skeleton"]')).toBeNull())
    expect(container.textContent).toBe("")
  })

  it("keeps a snapshot without statements: grid, the statement picker saying the provider has none, no table, no all-dash ratio strip", async () => {
    fundamentals.mockResolvedValue({ ...EMPTY, snapshot: DATA.snapshot })
    setup()
    expect(await screen.findByText("Piyasa değeri")).toBeInTheDocument()
    expect(screen.queryByRole("table")).toBeNull()
    expect(screen.queryByText("Türetilmiş oranlar", { exact: false })).toBeNull()
    expect(screen.getByRole("button", { name: "Bilanço" })).toBeInTheDocument()
    expect(screen.getByText("Sağlayıcıda bu dönem için bu tablo yok.")).toBeInTheDocument()
  })
})

describe("FundamentalsChips", () => {
  afterEach(() => { localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("renders only the figures Yahoo stated, market cap in the listing currency", () => {
    const { container } = setup("tr", <FundamentalsChips f={SUMMARY} />)
    const chips = Array.from(container.querySelectorAll("span.inline-flex")).map((c) => c.textContent)
    expect(chips).toEqual(["Piyasa değeri₺387 mr", "PD/DD1,9", "Net marj10,2%"])   // THYAO: TRY cap, no P/E (a loss), unsigned margin
  })

  it("renders nothing at all when only statements exist and no snapshot", () => {
    const { container } = setup("tr", <FundamentalsChips f={{ ...SUMMARY, market_cap: null, pe: null, price_to_book: null, net_margin: null, quote_currency: null }} />)
    expect(container.textContent).toBe("")
  })
})
