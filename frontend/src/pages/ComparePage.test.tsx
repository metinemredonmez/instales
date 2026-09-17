import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { FundCompare, FundOverlap, SearchHit } from "@/lib/api"
import { MarketProvider } from "@/lib/market"

const compare = vi.fn<(a: string, b: string) => Promise<FundCompare>>()
const fundOverlap = vi.fn<(codes: string[]) => Promise<FundOverlap>>()
const search = vi.fn<(market: string, q: string) => Promise<SearchHit[]>>()
vi.mock("@/lib/api", () => ({
  api: {
    compare: (a: string, b: string) => compare(a, b),
    fundOverlap: (codes: string[]) => fundOverlap(codes),
    search: (m: string, q: string) => search(m, q),
  },
}))

import { ComparePage, codesFromParams } from "./ComparePage"

const PAIR: FundCompare = {
  a: { code: "TMV", name: "Örnek Fon", institution: "İş Portföy" },
  b: { code: "MAC", name: "Mavi Fon", institution: "Ak Portföy" },
  common: [{ symbol: "ASELS", a_weight_pct: 4.2, b_weight_pct: 3.1, a_move: "ADD", b_move: "HOLD" }],
  only_a: ["THYAO"], only_b: ["GARAN", "EREGL"], both_increasing: ["ASELS"], both_reducing: [], opposite: [],
  overlap_pct: 25, overlap_pct_weighted: 18,
}

/** Three funds; MAC's report carries no weights, so its pairs have no weighted figure. */
const OVERLAP: FundOverlap = {
  as_of: "2026-09-15",
  funds: [
    { code: "TMV", name: "Örnek Fon", institution: "İş Portföy", as_of: "2026-09-15", holdings: 40 },
    { code: "MAC", name: "Mavi Fon", institution: "Ak Portföy", as_of: "2026-09-10", holdings: 25 },
    { code: "ABC", name: "Abc Fon", institution: "Abc Portföy", as_of: "2026-09-12", holdings: 30 },
  ],
  pairwise: [
    { a: "TMV", b: "MAC", overlap_pct_symbols: 42.3, overlap_pct_weighted: null },
    { a: "TMV", b: "ABC", overlap_pct_symbols: 30, overlap_pct_weighted: 12.5 },
    { a: "MAC", b: "ABC", overlap_pct_symbols: 20, overlap_pct_weighted: null },
  ],
  common_all: [
    { symbol: "ASELS", name: "Aselsan", weights: { TMV: 4.2, MAC: null, ABC: 2.8 } },
    { symbol: "THYAO", name: "Türk Hava Yolları", weights: { TMV: 1.1, MAC: null, ABC: 0.9 } },
  ],
}

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.search}</div> }

function setup(path = "/compare") {
  localStorage.setItem("instilens.market", "TR")
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MarketProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes><Route path="/compare" element={<><ComparePage /><Where /></>} /></Routes>
        </MemoryRouter>
      </MarketProvider>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

const where = () => screen.getByTestId("where").textContent

describe("codesFromParams", () => {
  it("reads ?codes= and the older ?a=&b= form, upper-cased, de-duplicated and cut at six", () => {
    expect(codesFromParams(new URLSearchParams("codes=tmv,MAC,,mac"))).toEqual(["TMV", "MAC"])
    expect(codesFromParams(new URLSearchParams("a=tmv&b=mac"))).toEqual(["TMV", "MAC"])
    expect(codesFromParams(new URLSearchParams("a=TMV"))).toEqual(["TMV"])
    expect(codesFromParams(new URLSearchParams("codes=A,B,C,D,E,F,G"))).toEqual(["A", "B", "C", "D", "E", "F"])
    expect(codesFromParams(new URLSearchParams(""))).toEqual([])
  })
})

describe("ComparePage", () => {
  beforeEach(() => {
    compare.mockReset(); fundOverlap.mockReset(); search.mockReset()
    compare.mockResolvedValue(PAIR); fundOverlap.mockResolvedValue(OVERLAP); search.mockResolvedValue([])
  })

  it("with three codes asks /funds/overlap and renders the pairwise matrix and the held-by-all table", async () => {
    setup("/compare?codes=TMV,MAC,ABC")
    expect(await screen.findByText("İkili örtüşme")).toBeInTheDocument()
    expect(fundOverlap).toHaveBeenLastCalledWith(["TMV", "MAC", "ABC"])
    expect(compare).not.toHaveBeenCalled()
    // Symmetric: TMV×ABC and ABC×TMV read the same; a pair without weights prints a dash under the symbol figure.
    const cell = (pair: string) => document.querySelector(`[data-pair="${pair}"]`)!
    expect(cell("TMV|ABC").textContent).toBe("30%12,5%")
    expect(cell("ABC|TMV").textContent).toBe("30%12,5%")
    expect(cell("TMV|MAC").textContent).toBe("42%—")
    expect(screen.getByText("Hepsinde ortak")).toBeInTheDocument()
    expect(screen.getByText("3 fonun tamamında · en küçük ağırlığa göre")).toBeInTheDocument()
    const row = within(screen.getByRole("link", { name: "ASELS" }).closest("tr")!).getAllByRole("cell").map((c) => c.textContent)
    expect(row).toEqual(["ASELSAselsan", "4,2%", "—", "2,8%"])
    expect(screen.getByText("40 pozisyon · 15 Eyl 2026")).toBeInTheDocument()
    // The chip and the fund card both link to the fund page.
    expect(screen.getAllByRole("link", { name: "ABC" }).map((l) => l.getAttribute("href"))).toEqual(["/funds/ABC", "/funds/ABC"])
  })

  it("with exactly two codes keeps the pair detail from /funds/{a}/compare/{b}, now with the weighted overlap", async () => {
    setup("/compare?a=TMV&b=MAC")
    expect(await screen.findByText("Ortak pozisyonlar")).toBeInTheDocument()
    expect(compare).toHaveBeenLastCalledWith("TMV", "MAC")
    expect(fundOverlap).not.toHaveBeenCalled()
    expect(screen.getByText("Örtüşme").nextElementSibling!.textContent).toBe("25%")
    expect(screen.getByText("Ağırlıklı örtüşme").nextElementSibling!.textContent).toBe("18,0%")
    expect(screen.queryByText("İkili örtüşme")).toBeNull()
  })

  it("adding a fund by code extends the URL set and switches to the overlap query; removing one goes back to the pair", async () => {
    const user = setup("/compare?a=TMV&b=MAC")
    await screen.findByText("Ortak pozisyonlar")
    await user.type(screen.getByRole("combobox", { name: "Fon ekle" }), "abc{Enter}")
    await waitFor(() => expect(where()).toBe("/compare?codes=TMV%2CMAC%2CABC"))
    await waitFor(() => expect(fundOverlap).toHaveBeenLastCalledWith(["TMV", "MAC", "ABC"]))
    expect(await screen.findByText("İkili örtüşme")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "ABC fonunu kaldır" }))
    await waitFor(() => expect(where()).toBe("/compare?codes=TMV%2CMAC"))
    expect(await screen.findByText("Ortak pozisyonlar")).toBeInTheDocument()
  })

  it("offers fund hits from /search, never stocks or funds already in the set, and picks one on click", async () => {
    search.mockResolvedValue([
      { kind: "stock", key: "ABCD", label: "ABCD", name: "Abcd A.Ş.", href: "/stocks/ABCD" },
      { kind: "fund", key: "TMV", label: "TMV", name: "Örnek Fon", href: "/funds/TMV" },
      { kind: "fund", key: "ABC", label: "ABC", name: "Abc Fon", href: "/funds/ABC" },
    ])
    const user = setup("/compare?codes=TMV,MAC")
    await screen.findByText("Ortak pozisyonlar")
    await user.type(screen.getByRole("combobox", { name: "Fon ekle" }), "ab")
    const options = await screen.findAllByRole("option")
    expect(options.map((o) => o.textContent)).toEqual(["ABCAbc Fon"])
    await user.click(options[0])
    await waitFor(() => expect(where()).toBe("/compare?codes=TMV%2CMAC%2CABC"))
  })

  it("asks for a second fund before querying anything", async () => {
    setup("/compare?a=TMV")
    expect(screen.getByText("Karşılaştırmak için en az iki fon seç.")).toBeInTheDocument()
    expect(compare).not.toHaveBeenCalled()
    expect(fundOverlap).not.toHaveBeenCalled()
  })

  it("closes the picker once six funds are in the set", async () => {
    setup("/compare?codes=A,B,C,D,E,F")
    expect(screen.getByRole("combobox", { name: "Fon ekle" })).toBeDisabled()
    expect(screen.getByRole("button", { name: "Fon ekle" })).toBeDisabled()
    expect(fundOverlap).toHaveBeenLastCalledWith(["A", "B", "C", "D", "E", "F"])
  })

  it("says a fund was not found on 404 and mixed markets on 422", async () => {
    fundOverlap.mockRejectedValue(new Error("404 fund not found"))
    setup("/compare?codes=TMV,MAC,XYZ")
    expect(await screen.findByText("Fonlardan biri bulunamadı.")).toBeInTheDocument()
    fundOverlap.mockRejectedValue(new Error("422 mixed markets"))
    setup("/compare?codes=TMV,MAC,NVDA")
    expect(await screen.findByText("Fonlar aynı piyasadan olmalı.")).toBeInTheDocument()
  })
})
