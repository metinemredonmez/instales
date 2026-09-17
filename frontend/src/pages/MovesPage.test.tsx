import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { MoveKind, Moves } from "@/lib/api"
import { MarketProvider } from "@/lib/market"

const moves = vi.fn<(market: string, kind: MoveKind, window: number | null, fund: string | null) => Promise<Moves>>()
vi.mock("@/lib/api", () => ({ api: { moves: (m: string, k: MoveKind, w: number | null, f: string | null) => moves(m, k, w, f) } }))

import { MovesPage } from "./MovesPage"

const DATA: Moves = {
  as_of: "2026-09-16", window_days: 30, window_start: "2026-08-17", kind: "buys", market: "TR", fund: null, total: 2,
  rows: [
    {
      symbol: "ASELS", name: "Aselsan", net_flow_value: 12_500_000, net_qty: 80_000,
      funds_increasing: 3, funds_reducing: 0, funds_new: 1, funds_exited: 0, party_count: 3,
      parties: [
        { kind: "fund", code: "TMV", name: "Örnek Fon", activity: "ADD", delta_qty: 50_000, delta_value: 7_500_000, to_weight_pct: 4.2, delta_weight_pct: 0.8, period_end: "2026-09-15", confidence: "INFERRED" },
        { kind: "institution", code: "ISP", name: "İş Portföy", activity: "NEW", delta_qty: 30_000, delta_value: 5_000_000, to_weight_pct: null, delta_weight_pct: null, period_end: "2026-09-10", confidence: "GROUPED" },
      ],
    },
    {
      symbol: "THYAO", name: "Türk Hava Yolları", net_flow_value: 3_000_000, net_qty: 10_000,
      funds_increasing: 1, funds_reducing: 0, funds_new: 0, funds_exited: 0, party_count: 1,
      parties: [{ kind: "fund", code: "MAC", name: "Mavi Fon", activity: "ADD", delta_qty: 10_000, delta_value: 3_000_000, to_weight_pct: 1.1, delta_weight_pct: 0.2, period_end: "2026-09-15", confidence: "EXACT" }],
    },
  ],
}

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.search}</div> }

function setup(path = "/moves", market: "TR" | "US" = "TR") {
  localStorage.setItem("instilens.market", market)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MarketProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes><Route path="/moves" element={<><MovesPage /><Where /></>} /></Routes>
        </MemoryRouter>
      </MarketProvider>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

describe("MovesPage", () => {
  beforeEach(() => { moves.mockReset(); moves.mockResolvedValue(DATA) })

  it("renders the rows with symbol, name, net flow and party chips", async () => {
    setup()
    expect(await screen.findByRole("link", { name: "ASELS" })).toHaveAttribute("href", "/stocks/ASELS")
    expect(moves).toHaveBeenLastCalledWith("TR", "buys", 30, null)   // TR default window, no fund
    expect(screen.getByText("Aselsan")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "THYAO" })).toHaveAttribute("href", "/stocks/THYAO")
    expect(screen.getByText("+₺12.5M")).toBeInTheDocument()
    const chip = screen.getByRole("link", { name: /Örnek Fon/ })
    expect(chip).toHaveAttribute("href", "/funds/TMV")
    expect(within(chip).getByText("ADD")).toBeInTheDocument()
    expect(within(chip).getByText("+₺7.5M")).toBeInTheDocument()
    expect(within(chip).queryByText("INFERRED")).toBeNull()          // inferred parties carry no confidence badge
    const grouped = screen.getByRole("link", { name: /İş Portföy/ })
    expect(grouped).toHaveAttribute("href", "/institutions/ISP")
    expect(within(grouped).getByText("GROUPED")).toBeInTheDocument()
    expect(screen.getByText("30 gün · 17 Ağu 2026 → 16 Eyl 2026 · 2 enstrüman")).toBeInTheDocument()
  })

  it("states the instrument count before the limit cut, not the page size", async () => {
    moves.mockResolvedValue({ ...DATA, total: 27 })
    setup()
    expect(await screen.findByText("27 enstrüman · ilk 2 gösteriliyor", { exact: false })).toBeInTheDocument()
  })

  it("US defaults to the 100D score window and offers the 13F-sized steps", async () => {
    const user = setup("/moves", "US")
    await screen.findByRole("link", { name: "ASELS" })
    expect(moves).toHaveBeenLastCalledWith("US", "buys", 100, null)
    expect(screen.getAllByRole("button", { pressed: true }).map((b) => b.textContent)).toEqual(["En çok alınan", "100G"])
    expect(screen.queryByRole("button", { name: "7G" })).toBeNull()
    await user.click(screen.getByRole("button", { name: "180G" }))
    await waitFor(() => expect(moves).toHaveBeenLastCalledWith("US", "buys", 180, null))
  })

  it("switching kind and window changes the query arguments", async () => {
    const user = setup()
    await screen.findByRole("link", { name: "ASELS" })
    await user.click(screen.getByRole("button", { name: "En çok satılan" }))
    await waitFor(() => expect(moves).toHaveBeenLastCalledWith("TR", "sells", 30, null))
    expect(screen.getByRole("button", { name: "En çok satılan" })).toHaveAttribute("aria-pressed", "true")
    await user.click(screen.getByRole("button", { name: "90G" }))
    await waitFor(() => expect(moves).toHaveBeenLastCalledWith("TR", "sells", 90, null))
    await user.click(screen.getByRole("button", { name: "Kapanan pozisyon" }))
    await waitFor(() => expect(moves).toHaveBeenLastCalledWith("TR", "exits", 90, null))
  })

  it("?fund= narrows the query and shows a chip that links to the fund and clears the filter", async () => {
    moves.mockResolvedValue({ ...DATA, fund: { code: "TMV", name: "Örnek Fon" }, rows: [DATA.rows[0]] })
    const user = setup("/moves?fund=TMV")
    const chip = await screen.findByRole("link", { name: "Örnek Fon" })
    expect(chip).toHaveAttribute("href", "/funds/TMV")
    expect(moves).toHaveBeenLastCalledWith("TR", "buys", 30, "TMV")
    await user.click(screen.getByRole("button", { name: "Fon filtresini kaldır" }))
    await waitFor(() => expect(moves).toHaveBeenLastCalledWith("TR", "buys", 30, null))
    expect(screen.getByTestId("where").textContent).toBe("/moves")
    expect(screen.queryByText("Fon filtresi:")).toBeNull()
  })

  it("a lowercase ?fund= still matches the fund the API names", async () => {
    moves.mockResolvedValue({ ...DATA, fund: { code: "TMV", name: "Örnek Fon" }, rows: [DATA.rows[0]] })
    setup("/moves?fund=tmv")
    expect(await screen.findByRole("link", { name: "Örnek Fon" })).toHaveAttribute("href", "/funds/TMV")
    expect(moves).toHaveBeenLastCalledWith("TR", "buys", 30, "TMV")
  })

  it("shows the empty state when the window has no moves", async () => {
    moves.mockResolvedValue({ ...DATA, total: 0, rows: [] })
    setup()
    expect(await screen.findByText("Bu pencerede hareket yok.")).toBeInTheDocument()
  })
})
