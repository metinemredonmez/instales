import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { FilingForm, Filings as FilingsData } from "@/lib/api"
import { LangProvider } from "@/lib/i18n"
import { setLocale } from "@/lib/format"

const filings = vi.fn<(market: string, symbol: string, form: FilingForm | null, limit: number) => Promise<FilingsData>>()
vi.mock("@/lib/api", () => ({ api: { filings: (m: string, s: string, f: FilingForm | null, l: number) => filings(m, s, f, l) } }))

import { Filings } from "./Filings"

const EDGAR = "https://www.sec.gov/Archives/edgar/data/320193"
/** An earnings 8-K with two items, a 10-Q with a period, an 8-K carrying an item without a title of its own, and a Form 4 without a primary document. */
const DATA: FilingsData = {
  symbol: "AAPL", supported: true, fetched_at: "2026-09-16T05:30:00Z",
  filings: [
    { form: "8-K", filed_at: "2026-07-31", period: null, items: ["2.02", "9.01"], accession: "0000320193-26-000071", url: `${EDGAR}/0000320193-26-000071-index.htm`, primary_url: `${EDGAR}/000032019326000071/a8-kex991q3.htm` },
    { form: "10-Q", filed_at: "2026-07-31", period: "2026-06-27", items: [], accession: "0000320193-26-000072", url: `${EDGAR}/0000320193-26-000072-index.htm`, primary_url: `${EDGAR}/000032019326000072/aapl-20260627.htm` },
    { form: "8-K", filed_at: "2026-06-10", period: null, items: ["3.03"], accession: "0000320193-26-000060", url: `${EDGAR}/0000320193-26-000060-index.htm`, primary_url: null },
    { form: "4", filed_at: "2026-05-05", period: null, items: [], accession: "0001234567-26-000004", url: `${EDGAR}/0001234567-26-000004-index.htm`, primary_url: null },
  ],
}

function setup(lang: "tr" | "en" = "tr", ui: React.ReactNode = <Filings symbol="AAPL" market="US" />) {
  localStorage.setItem("instilens.lang", lang)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={qc}>
      <LangProvider>{ui}</LangProvider>
    </QueryClientProvider>,
  )
  return { user: userEvent.setup(), ...utils }
}

describe("Filings", () => {
  beforeEach(() => { filings.mockReset(); filings.mockResolvedValue(DATA) })
  afterEach(() => { localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("lists the latest filings with form chip, date, period, item chips and the EDGAR link", async () => {
    setup()
    expect(await screen.findByText("EDGAR dosyalamaları")).toBeInTheDocument()
    expect(filings).toHaveBeenLastCalledWith("US", "AAPL", null, 10)
    const rows = screen.getAllByRole("listitem")
    expect(rows).toHaveLength(4)
    // The earnings 8-K: the form chip opens the main document, the items carry their titles, the trailing link the index page.
    const earnings = within(rows[0])
    expect(earnings.getByRole("link", { name: "8-K" })).toHaveAttribute("href", `${EDGAR}/000032019326000071/a8-kex991q3.htm`)
    expect(earnings.getByText("31 Tem 2026")).toBeInTheDocument()
    expect(earnings.getByText("2.02").parentElement!.textContent).toBe("2.02Faaliyet sonuçları")
    expect(earnings.getByText("9.01").parentElement!.textContent).toBe("9.01Ekler")
    const index = earnings.getByRole("link", { name: /EDGAR/ })
    expect(index).toHaveAttribute("href", `${EDGAR}/0000320193-26-000071-index.htm`)
    expect(index).toHaveAttribute("target", "_blank")
    expect(index.getAttribute("rel")).toContain("noopener")
    // The 10-Q states its period; an 8-K item without a title of its own shows the code alone; a filing without a primary document has a plain chip.
    expect(within(rows[1]).getByText("dönem 06/2026")).toBeInTheDocument()
    expect(within(rows[2]).getByText("3.03").parentElement!.textContent).toBe("3.03")
    expect(within(rows[3]).queryByRole("link", { name: "4" })).toBeNull()
    expect(within(rows[3]).getByText("4")).toBeInTheDocument()
    expect(screen.getByText("Kaynak: SEC EDGAR")).toBeInTheDocument()
  })

  it("the form filter re-queries with that form; 'all' sends none", async () => {
    const { user } = setup()
    await screen.findByText("EDGAR dosyalamaları")
    expect(screen.getByRole("button", { name: "Tümü" })).toHaveAttribute("aria-pressed", "true")
    filings.mockResolvedValue({ ...DATA, filings: DATA.filings.filter((f) => f.form === "8-K") })
    await user.click(screen.getByRole("button", { name: "8-K" }))
    await waitFor(() => expect(filings).toHaveBeenLastCalledWith("US", "AAPL", "8-K", 10))
    expect(screen.getByRole("button", { name: "8-K" })).toHaveAttribute("aria-pressed", "true")
    await waitFor(() => expect(screen.getAllByRole("listitem")).toHaveLength(2))
    await user.click(screen.getByRole("button", { name: "10-K" }))
    await waitFor(() => expect(filings).toHaveBeenLastCalledWith("US", "AAPL", "10-K", 10))
    await user.click(screen.getByRole("button", { name: "Tümü" }))
    await waitFor(() => expect(screen.getByRole("button", { name: "Tümü" })).toHaveAttribute("aria-pressed", "true"))
  })

  it("shows the empty state under a filter that matches nothing, keeping the filter", async () => {
    filings.mockResolvedValue({ ...DATA, filings: [] })
    setup()
    expect(await screen.findByText("Bu filtrede dosyalama yok.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "10-Q" })).toBeInTheDocument()
  })

  it("says the issuer has not been read yet (fetched_at null) instead of claiming nothing was filed", async () => {
    filings.mockResolvedValue({ ...DATA, fetched_at: null, filings: [] })
    setup()
    expect(await screen.findByText(/EDGAR dosyalamaları henüz okunmadı/)).toBeInTheDocument()
    expect(screen.queryByText("Bu filtrede dosyalama yok.")).toBeNull()
  })

  it("hides itself on a BIST symbol, where the API answers supported:false", async () => {
    filings.mockResolvedValue({ symbol: "ASELS", supported: false, fetched_at: null, filings: [] })
    const { container } = setup("tr", <Filings symbol="ASELS" market="TR" />)
    await waitFor(() => expect(filings).toHaveBeenLastCalledWith("TR", "ASELS", null, 10))
    await waitFor(() => expect(container.querySelector('[data-slot="skeleton"]')).toBeNull())
    expect(container.textContent).toBe("")
  })

  it("hides itself on an API error instead of rendering a broken box", async () => {
    filings.mockRejectedValue(new Error("404 unknown symbol"))
    const { container } = setup()
    await waitFor(() => expect(filings).toHaveBeenCalled())
    await waitFor(() => expect(container.querySelector('[data-slot="skeleton"]')).toBeNull())
    expect(container.textContent).toBe("")
  })

  it("keeps the loaded list when a filter fails to load, and says so", async () => {
    const { user } = setup()
    await screen.findByText("EDGAR dosyalamaları")
    filings.mockRejectedValueOnce(new Error("502"))
    await user.click(screen.getByRole("button", { name: "4" }))
    expect(await screen.findByText("Dosyalamalar yüklenemedi.")).toBeInTheDocument()
    expect(screen.getAllByRole("listitem")).toHaveLength(4)
  })

  it("renders English labels", async () => {
    setup("en")
    expect(await screen.findByText("EDGAR filings")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "All" })).toHaveAttribute("aria-pressed", "true")
    const rows = screen.getAllByRole("listitem")
    expect(within(rows[0]).getByText("2.02").parentElement!.textContent).toBe("2.02Results of operations")
    expect(within(rows[0]).getByText("9.01").parentElement!.textContent).toBe("9.01Exhibits")
    expect(within(rows[1]).getByText("period 06/2026")).toBeInTheDocument()
    expect(screen.getByText("Source: SEC EDGAR")).toBeInTheDocument()
  })
})
