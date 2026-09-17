import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { TextKind, TextSearch } from "@/lib/api"
import { MarketProvider } from "@/lib/market"

const searchText = vi.fn<(market: string, q: string, kinds: TextKind[] | null, limit: number) => Promise<TextSearch>>()
vi.mock("@/lib/api", () => ({ api: { searchText: (m: string, q: string, k: TextKind[] | null, l: number) => searchText(m, q, k, l) } }))

import { SearchPage } from "./SearchPage"

/** A KAP disclosure with its stock page and source, and a news item that has only its publisher's page. */
const DATA: TextSearch = {
  q: "temettü", market: "TR", total: 2,
  hits: [
    { kind: "disclosure", id: 11, title: "Kar payı dağıtımına ilişkin bildirim", snippet: "Yönetim kurulu «temettü» ödemesini görüştü.", date: "2026-09-10", symbols: ["ASELS"], source: "KAP", url: "https://www.kap.org.tr/tr/Bildirim/11", link: "/stocks/ASELS", superseded: false, score: 0.9 },
    { kind: "news", id: 5, title: "Borsada temettü takvimi", snippet: "…«temettü» verimi yüksek şirketler…", date: "2026-09-12", symbols: [], source: "Örnek Haber", url: "https://example.com/n/5", link: null, superseded: false, score: 0.4 },
  ],
}
/** A portfolio report with more symbols than a row shows, the notice a correction replaced, an AI note without a page. */
const MORE: TextSearch = {
  q: "temettü", market: "TR", total: 3,
  hits: [
    { kind: "disclosure", id: 12, title: "Fon Portföy Dağılım Raporu · TMV", snippet: "«temettü» geliri", date: "2026-09-09", symbols: ["ASELS", "SASA", "EREGL", "KCHOL", "THYAO", "GARAN", "AKBNK", "SISE"], source: "KAP", url: "https://www.kap.org.tr/tr/Bildirim/12", link: "/funds/TMV", superseded: false, score: 0.8 },
    { kind: "note", id: 3, title: "ASELS: 3 fon artırdı", snippet: "«temettü» beklentisi", date: "2026-09-08", symbols: ["ASELS"], source: "InstiLens AI", url: null, link: null, superseded: false, score: 0.5 },
    { kind: "disclosure", id: 9, title: "Düzeltildi — Pay Alım Satım Bildirimi · ANELE · Tera Portföy", snippet: "«temettü» sonrası alış", date: "2026-09-07", symbols: ["ANELE"], source: "KAP", url: "https://www.kap.org.tr/tr/Bildirim/9", link: "/stocks/ANELE", superseded: true, score: 0.9 },
  ],
}

function Where() { const l = useLocation(); return <div data-testid="where">{l.pathname + l.search}</div> }

function setup(path = "/search?q=temettü", market = "TR") {
  localStorage.setItem("instilens.market", market)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(
    <QueryClientProvider client={qc}>
      <MarketProvider>
        <MemoryRouter initialEntries={[path]}>
          <Routes><Route path="/search" element={<><SearchPage /><Where /></>} /></Routes>
        </MemoryRouter>
      </MarketProvider>
    </QueryClientProvider>,
  )
  return userEvent.setup()
}

describe("SearchPage", () => {
  beforeEach(() => { searchText.mockReset(); searchText.mockResolvedValue(DATA) })

  it("renders the hits with kind, links, highlighted snippet, date, source and symbol chips", async () => {
    setup()
    const kap = await screen.findByRole("link", { name: "Kar payı dağıtımına ilişkin bildirim" })
    expect(searchText).toHaveBeenLastCalledWith("TR", "temettü", null, 20)
    expect(screen.getByRole("textbox")).toHaveValue("temettü")           // prefilled from the URL
    expect(kap).toHaveAttribute("href", "/stocks/ASELS")                  // in-app route wins over the source page
    const news = screen.getByRole("link", { name: "Borsada temettü takvimi" })
    expect(news).toHaveAttribute("href", "https://example.com/n/5")       // no route → the publisher's page
    expect(news).toHaveAttribute("rel", "noopener noreferrer")
    const rows = screen.getAllByRole("listitem")
    expect(rows).toHaveLength(2)
    const marks = rows[0].querySelectorAll("mark")
    expect(Array.from(marks).map((m) => m.textContent)).toEqual(["temettü"])
    expect(rows[0].textContent).toContain("Yönetim kurulu temettü ödemesini görüştü.")   // the «» markers are not shown
    expect(within(rows[0]).getByText("Bildirim")).toBeInTheDocument()
    expect(within(rows[0]).getByText("10 Eyl 2026")).toBeInTheDocument()
    expect(within(rows[0]).getByRole("link", { name: /KAP/ })).toHaveAttribute("href", "https://www.kap.org.tr/tr/Bildirim/11")
    expect(within(rows[0]).getByRole("link", { name: "ASELS" })).toHaveAttribute("href", "/stocks/ASELS")
    expect(within(rows[1]).getByText("Haber")).toBeInTheDocument()
    expect(within(rows[1]).getByText("Örnek Haber")).toBeInTheDocument()
    expect(screen.getByText("2 sonuç")).toBeInTheDocument()
    expect(screen.queryByText("Daha fazla göster")).toBeNull()                 // every hit is on the page
  })

  it("caps the symbol chips, marks a superseded notice and leaves an AI note unlinked", async () => {
    searchText.mockResolvedValue(MORE)
    setup()
    const rows = await screen.findAllByRole("listitem")
    expect(within(rows[0]).getAllByRole("link", { name: /^[A-Z]{4,5}$/ }).map((a) => a.textContent)).toEqual(["ASELS", "SASA", "EREGL", "KCHOL", "THYAO", "GARAN"])
    expect(within(rows[0]).getByText("+2 daha")).toBeInTheDocument()
    expect(within(rows[1]).queryByRole("link", { name: "ASELS: 3 fon artırdı" })).toBeNull()   // the stock page shows the current note, not this one
    expect(within(rows[1]).getByText("ASELS: 3 fon artırdı")).toBeInTheDocument()
    expect(within(rows[1]).getByRole("link", { name: "ASELS" })).toHaveAttribute("href", "/stocks/ASELS")
    expect(within(rows[2]).getByText("Düzeltildi")).toBeInTheDocument()
    expect(within(rows[0]).queryByText("Düzeltildi")).toBeNull()
  })

  it("kind chips narrow the query and the URL; every kind picked or “all” clears the filter", async () => {
    const user = setup()
    await screen.findByRole("link", { name: "ASELS" })
    expect(screen.getByRole("button", { name: "Tümü" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.queryByRole("button", { name: "Dosyalamalar" })).toBeNull()   // EDGAR filings are US only: no dead chip in TR
    await user.click(screen.getByRole("button", { name: "Haberler" }))
    await waitFor(() => expect(searchText).toHaveBeenLastCalledWith("TR", "temettü", ["news"], 20))
    expect(screen.getByRole("button", { name: "Haberler" })).toHaveAttribute("aria-pressed", "true")
    expect(screen.getByRole("button", { name: "Tümü" })).toHaveAttribute("aria-pressed", "false")
    expect(screen.getByTestId("where").textContent).toBe("/search?q=temett%C3%BC&kinds=news")
    await user.click(screen.getByRole("button", { name: "Bildirimler" }))
    await waitFor(() => expect(searchText).toHaveBeenLastCalledWith("TR", "temettü", ["disclosure", "news"], 20))   // the API's order, not click order
    await user.click(screen.getByRole("button", { name: "Tümü" }))
    await waitFor(() => expect(searchText).toHaveBeenLastCalledWith("TR", "temettü", null, 20))
    expect(screen.getByTestId("where").textContent).toBe("/search?q=temett%C3%BC")
  })

  it("in the US market the filings chip exists and the subtitle names EDGAR; a TR URL asking for filings is ignored", async () => {
    setup("/search?q=apple&kinds=filing,news", "US")
    await screen.findByRole("link", { name: "ASELS" })
    expect(screen.getByRole("button", { name: "Dosyalamalar" })).toHaveAttribute("aria-pressed", "true")
    expect(searchText).toHaveBeenLastCalledWith("US", "apple", ["filing", "news"], 20)
    expect(screen.getByText(/EDGAR dosyalamaları/)).toBeInTheDocument()
    setup("/search?q=apple&kinds=filing,news", "TR")
    await waitFor(() => expect(searchText).toHaveBeenLastCalledWith("TR", "apple", ["news"], 20))
  })

  it("typing writes the query to the URL after the debounce; a query under two characters asks nothing", async () => {
    const user = setup("/search")
    expect(screen.getByText("Aramak için en az 2 karakter yaz.")).toBeInTheDocument()
    await user.type(screen.getByRole("textbox"), "bedelsiz")
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/search?q=bedelsiz"))
    await waitFor(() => expect(searchText).toHaveBeenLastCalledWith("TR", "bedelsiz", null, 20))
    expect(searchText).toHaveBeenCalledTimes(1)                            // one call for the settled text, not per keystroke
    await user.clear(screen.getByRole("textbox"))
    await user.type(screen.getByRole("textbox"), "b")
    await waitFor(() => expect(screen.getByTestId("where").textContent).toBe("/search?q=b"))
    expect(screen.getByText("Aramak için en az 2 karakter yaz.")).toBeInTheDocument()
    expect(searchText).toHaveBeenCalledTimes(1)
  })

  it("shows the empty state with the market, a capped total as “500+”, and “show more” once up to the API's 50", async () => {
    searchText.mockResolvedValue({ ...DATA, total: 0, hits: [] })
    setup()
    expect(await screen.findByText("BIST (TR) piyasasında “temettü” için sonuç yok.")).toBeInTheDocument()
    searchText.mockResolvedValue({ ...DATA, total: 500 })
    const user = setup("/search?q=kar")
    expect(await screen.findByText("500+ sonuç · ilk 2 gösteriliyor")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Daha fazla göster" }))
    await waitFor(() => expect(searchText).toHaveBeenLastCalledWith("TR", "kar", null, 50))
    await waitFor(() => expect(screen.queryByRole("button", { name: "Daha fazla göster" })).toBeNull())   // 50 is the API's maximum: the count is the cue to narrow the query
    await user.click(screen.getAllByRole("button", { name: "Haberler" }).at(-1)!)   // the second tree's chip (the first still shows its empty state)
    await waitFor(() => expect(searchText).toHaveBeenLastCalledWith("TR", "kar", ["news"], 20))   // a new filter starts at the first page again
  })
})
