import { render, screen, waitFor, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Insiders as InsidersData, InsiderTx, InsiderWindow } from "@/lib/api"
import { LangProvider } from "@/lib/i18n"
import { setLocale } from "@/lib/format"

const insiders = vi.fn<(market: string, symbol: string, days: InsiderWindow) => Promise<InsidersData>>()
vi.mock("@/lib/api", () => ({ api: { insiders: (m: string, s: string, d: InsiderWindow) => insiders(m, s, d) } }))

import { Insiders, InsidersChip } from "./Insiders"

const EDGAR = "https://www.sec.gov/Archives/edgar/data/320193"
const EDGAR_FORM4S = "https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=320193&type=4&dateb=&owner=include&count=100"
const tx = (id: number, over: Partial<InsiderTx>): InsiderTx => ({
  id, transaction_date: "2026-09-10", filed_at: "2026-09-12", insider: "Jane Roe", insider_cik: "0001234567", role: "officer", title: "Chief Financial Officer",
  code: "P", acquired: true, shares: 1_200, price: 187.5, value: 225_000, post_shares: 41_200, ownership: "D", derivative: false, confidence: "EXACT",
  accession: `0001234567-26-${String(id).padStart(6, "0")}`, url: `${EDGAR}/0001234567-26-${String(id).padStart(6, "0")}-index.htm`,
  ...over,
})

/** A purchase, a sale, a grant without a price and a derivative exercise — the four shapes the table distinguishes. */
const DATA: InsidersData = {
  symbol: "AAPL", name: "Apple Inc.", market: "US", supported: true, days: 90, as_of: "2026-09-16T12:00:00Z", source: "sec-edgar", fetched_at: "2026-09-16T05:30:00Z",
  truncated: false, edgar_url: EDGAR_FORM4S,
  summary: { buyers: 3, sellers: 1, buy_value: 1_240_000, sell_value: 951_250, net_value: 288_750, open_market_buys: 3, open_market_sells: 1, cluster: { since: "2026-08-17", insiders: 3, value: 1_240_000 } },
  transactions: [
    tx(1, {}),
    tx(2, { insider: "John Doe", role: "director", title: null, code: "S", acquired: false, shares: 5_000, price: 190.25, value: 951_250, post_shares: 10_000, ownership: "I", transaction_date: "2026-09-08", filed_at: "2026-09-09" }),
    tx(3, { insider: "Ann Lee", role: "director,officer", title: "Chief Executive Officer", code: "A", acquired: true, shares: 2_500, price: null, value: null, post_shares: null, transaction_date: "2026-09-01", filed_at: "2026-09-03" }),
    tx(4, { insider: "Ann Lee", role: "director,officer", title: "Chief Executive Officer", code: "M", acquired: true, shares: 800, price: 42, value: 33_600, post_shares: 3_800, derivative: true, transaction_date: "2026-08-20", filed_at: "2026-08-22" }),
  ],
}

const EMPTY: InsidersData = { ...DATA, summary: { buyers: 0, sellers: 0, buy_value: 0, sell_value: 0, net_value: 0, open_market_buys: 0, open_market_sells: 0, cluster: null }, transactions: [] }

function setup(lang: "tr" | "en" = "tr", ui: React.ReactNode = <Insiders symbol="AAPL" market="US" />) {
  localStorage.setItem("instilens.lang", lang)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const utils = render(
    <QueryClientProvider client={qc}>
      <LangProvider>{ui}</LangProvider>
    </QueryClientProvider>,
  )
  return { user: userEvent.setup(), ...utils }
}

const rowOf = (name: string) => within(screen.getByText(name).closest("tr")!)
const cells = (name: string) => rowOf(name).getAllByRole("cell").map((c) => c.textContent)

describe("Insiders", () => {
  beforeEach(() => { insiders.mockReset(); insiders.mockResolvedValue(DATA) })
  afterEach(() => { localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("renders the 90-day summary strip with signed net value and the cluster chip", async () => {
    setup()
    expect(await screen.findByText("İçeriden işlemler")).toBeInTheDocument()
    expect(insiders).toHaveBeenLastCalledWith("US", "AAPL", 90)
    expect(screen.getByText("Son 90 gün · 16 Eyl 2026")).toBeInTheDocument()
    const chip = (label: string) => screen.getByText(label).nextElementSibling!
    expect(chip("Alıcı").textContent).toBe("3 kişi")
    expect(chip("Satıcı").textContent).toBe("1 kişi")
    expect(chip("Net değer").textContent).toBe("+$289 bin")
    expect(chip("Net değer")).toHaveClass("text-positive")
    expect(chip("Açık piyasa").textContent).toBe("3 alım · 1 satım")
    // 2026-08-17 → 2026-09-16 is 30 days: the signal's own window, stated with the cluster's value.
    expect(screen.getByText(/^▲ Küme: 30 günde 3 şirket içi kişi açık piyasadan aldı ·/).textContent).toBe("▲ Küme: 30 günde 3 şirket içi kişi açık piyasadan aldı · $1,2 mn")
  })

  it("lists the transactions newest first with code labels, signed shares and value, price, ownership and the EDGAR link", async () => {
    setup()
    const table = await screen.findByRole("table")
    expect(within(table).getAllByRole("columnheader").map((h) => h.textContent)).toEqual(["Tarih", "Kişi", "İşlem türü", "Pay", "Fiyat", "Değer", "İşlem sonrası pay", "Sahiplik", ""])
    expect(within(table).getAllByRole("row").slice(1).map((r) => within(r).getAllByRole("cell")[1].textContent)).toEqual(["Jane RoeYönetici · Chief Financial Officer", "John DoeYönetim kurulu üyesi", "Ann LeeYönetim kurulu üyesi, Yönetici · Chief Executive Officer", "Ann LeeYönetim kurulu üyesi, Yönetici · Chief Executive Officer"])
    expect(cells("Jane Roe")).toEqual(["10 Eyl 2026bildirim 12 Eyl 2026", "Jane RoeYönetici · Chief Financial Officer", "PAçık piyasa alımı", "+1.200", "$187,50", "+$225 bin", "41.200", "Doğrudan", ""])
    expect(cells("John Doe")).toEqual(["08 Eyl 2026bildirim 09 Eyl 2026", "John DoeYönetim kurulu üyesi", "SAçık piyasa satımı", "-5.000", "$190,25", "-$951 bin", "10.000", "Dolaylı", ""])
    const link = rowOf("Jane Roe").getByRole("link", { name: "EDGAR'da aç" })
    expect(link).toHaveAttribute("href", `${EDGAR}/0001234567-26-000001-index.htm`)
    expect(link).toHaveAttribute("target", "_blank")
    expect(link.getAttribute("rel")).toContain("noopener")
  })

  it("colours only open-market buys and sells; a grant and an exercise stay neutral, the exercise wears the derivative badge", async () => {
    setup()
    await screen.findByRole("table")
    const buy = rowOf("Jane Roe").getAllByRole("cell")
    expect(buy[2]).toHaveClass("text-positive")
    expect(buy[3]).toHaveClass("text-positive")
    expect(buy[5]).toHaveClass("text-positive")
    const sell = rowOf("John Doe").getAllByRole("cell")
    expect(sell[2]).toHaveClass("text-negative")
    expect(sell[5]).toHaveClass("text-negative")
    const grant = within(screen.getByText("Hibe/ödül").closest("tr")!).getAllByRole("cell")
    expect(grant[2].textContent).toBe("AHibe/ödül")
    expect(grant[2]).not.toHaveClass("text-positive")
    expect(grant[2]).not.toHaveClass("text-negative")
    expect(grant[4].textContent).toBe("—")          // no price on the filing
    expect(grant[5].textContent).toBe("—")          // hence no value
    expect(grant[6].textContent).toBe("—")
    expect(grant[3].textContent).toBe("+2.500")     // still an acquisition
    const exercise = within(screen.getByText("Opsiyon kullanımı / RSU teslimi").closest("tr")!)
    expect(exercise.getByText("Türev")).toBeInTheDocument()
    expect(exercise.getByText("Opsiyon kullanımı / RSU teslimi").closest("td")).not.toHaveClass("text-positive")
    expect(screen.getAllByText("Türev")).toHaveLength(1)    // the badge marks the derivative row only
  })

  it("the window toggle re-queries with 180 and 365 days", async () => {
    const { user } = setup()
    await screen.findByRole("table")
    expect(screen.getByRole("button", { name: "90G" })).toHaveAttribute("aria-pressed", "true")
    await user.click(screen.getByRole("button", { name: "180G" }))
    await waitFor(() => expect(insiders).toHaveBeenLastCalledWith("US", "AAPL", 180))
    expect(screen.getByRole("button", { name: "180G" })).toHaveAttribute("aria-pressed", "true")
    await user.click(screen.getByRole("button", { name: "1Y" }))
    await waitFor(() => expect(insiders).toHaveBeenLastCalledWith("US", "AAPL", 365))
  })

  it("hides itself on a BIST symbol, where the API answers supported:false", async () => {
    insiders.mockResolvedValue({ supported: false, symbol: "ASELS", market: "TR" })
    const { container } = setup("tr", <Insiders symbol="ASELS" market="TR" />)
    await waitFor(() => expect(insiders).toHaveBeenLastCalledWith("TR", "ASELS", 90))
    await waitFor(() => expect(container.querySelector('[data-slot="skeleton"]')).toBeNull())
    expect(container.textContent).toBe("")
  })

  it("hides itself on an API error instead of rendering a broken box", async () => {
    insiders.mockRejectedValue(new Error("404 unknown symbol"))
    const { container } = setup()
    await waitFor(() => expect(insiders).toHaveBeenCalled())
    await waitFor(() => expect(container.querySelector('[data-slot="skeleton"]')).toBeNull())
    expect(container.textContent).toBe("")
  })

  it("keeps the loaded window on screen when the next one fails, and says so", async () => {
    const { user } = setup()
    await screen.findByRole("table")
    insiders.mockRejectedValueOnce(new Error("502"))
    await user.click(screen.getByRole("button", { name: "180G" }))
    expect(await screen.findByText("Bu pencere yüklenemedi.")).toBeInTheDocument()
    expect(screen.getByRole("table")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "90G" }))
    await waitFor(() => expect(screen.queryByText("Bu pencere yüklenemedi.")).toBeNull())
  })

  it("shows the empty state for the window, keeping the toggle so a wider window is one click away", async () => {
    insiders.mockResolvedValue(EMPTY)
    setup()
    expect(await screen.findByText("Son 90 günde raporlanmış içeriden işlem yok.")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "1Y" })).toBeInTheDocument()
    expect(screen.queryByRole("table")).toBeNull()
    expect(screen.queryByText(/Kaynak:/)).toBeNull()
  })

  it("says the issuer has not been read yet (fetched_at null) instead of claiming there was no activity", async () => {
    insiders.mockResolvedValue({ ...EMPTY, fetched_at: null })
    setup()
    expect(await screen.findByText(/Form 4 bildirimleri henüz okunmadı/)).toBeInTheDocument()
    expect(screen.queryByText("Son 90 günde raporlanmış içeriden işlem yok.")).toBeNull()
    expect(screen.getByRole("button", { name: "180G" })).toBeInTheDocument()
  })

  it("folds a long list after 30 rows behind a 'show all' button", async () => {
    const many = Array.from({ length: 35 }, (_, i) => tx(100 + i, { insider: `Insider ${i}` }))
    insiders.mockResolvedValue({ ...DATA, transactions: many })
    const { user } = setup()
    const table = await screen.findByRole("table")
    expect(within(table).getAllByRole("row")).toHaveLength(31)
    await user.click(screen.getByRole("button", { name: "Tümünü göster (35)" }))
    expect(within(table).getAllByRole("row")).toHaveLength(36)
    expect(screen.queryByRole("button", { name: /Tümünü göster/ })).toBeNull()
    expect(screen.queryByText(/gerisi EDGAR'da/)).toBeNull()  // the window fits: no truncation note
  })

  it("says when the API cut the window at its cap, linking the issuer's Form 4 list on EDGAR", async () => {
    const many = Array.from({ length: 200 }, (_, i) => tx(100 + i, { insider: `Insider ${i}` }))
    insiders.mockResolvedValue({ ...DATA, transactions: many, truncated: true })
    setup()
    await screen.findByRole("table")
    const note = screen.getByRole("link", { name: "İlk 200 işlem · gerisi EDGAR'da" })
    expect(note).toHaveAttribute("href", EDGAR_FORM4S)
    expect(note).toHaveAttribute("target", "_blank")
  })

  it("labels the uncommon codes as the form defines them, never as a short position", async () => {
    insiders.mockResolvedValue({ ...DATA, transactions: [tx(1, { code: "X" }), tx(2, { code: "D", acquired: false }), tx(3, { code: "W" }), tx(4, { code: "K" })] })
    setup()
    await screen.findByRole("table")
    expect(screen.getByText("Kârdaki türevin kullanımı")).toBeInTheDocument()
    expect(screen.getByText("İhraççıya devir")).toBeInTheDocument()
    expect(screen.getByText("Miras/vasiyet")).toBeInTheDocument()
    expect(screen.getByText("K").closest("td")!.textContent).toBe("KDiğer")
  })

  it("names the source and the as-reported disclaimer", async () => {
    setup()
    await screen.findByRole("table")
    expect(screen.getByText("Kaynak: SEC EDGAR Form 4 · işlem kodları raporlandığı gibi · tavsiye değildir")).toBeInTheDocument()
  })

  it("renders English labels and English compact money", async () => {
    setup("en")
    expect(await screen.findByText("Insider transactions")).toBeInTheDocument()
    expect(screen.getByText("Last 90 days · 16 Sept 2026")).toBeInTheDocument()
    const chip = (label: string) => screen.getByText(label).nextElementSibling!
    expect(chip("Buyers").textContent).toBe("3")
    expect(chip("Net value").textContent).toBe("+$289 k")
    expect(screen.getByText(/^▲ Cluster: 3 insiders bought on the open market within 30 days ·/)).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "90D" })).toHaveAttribute("aria-pressed", "true")
    expect(cells("Jane Roe")).toEqual(["10 Sept 2026filed 12 Sept 2026", "Jane RoeOfficer · Chief Financial Officer", "POpen-market purchase", "+1,200", "$187.50", "+$225 k", "41,200", "Direct", ""])
    expect(screen.getByText("Derivative")).toBeInTheDocument()
    expect(screen.getByText("Source: SEC EDGAR Form 4 · transaction codes as reported · not advice")).toBeInTheDocument()
  })
})

describe("InsidersChip", () => {
  afterEach(() => { localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("prints buyers and sellers over the window; a live cluster tints the chip and names the signal", () => {
    const { container } = setup("tr", <InsidersChip s={{ days: 90, buyers: 3, sellers: 1, net_value: 288_750, cluster: true }} />)
    const chip = container.querySelector("span.inline-flex")!
    expect(chip.textContent).toBe("İçeriden (90G)+3/−1")
    expect(chip).toHaveClass("border-positive/40")
    expect(chip).toHaveAttribute("title", "İçeriden alım kümesi")
  })

  it("stays neutral without a cluster; English window suffix", () => {
    const { container } = setup("en", <InsidersChip s={{ days: 90, buyers: 0, sellers: 2, net_value: -40_000, cluster: false }} />)
    const chip = container.querySelector("span.inline-flex")!
    expect(chip.textContent).toBe("Insiders (90D)+0/−2")
    expect(chip).not.toHaveClass("border-positive/40")
    expect(chip).not.toHaveAttribute("title")
  })

  it("renders nothing for an issuer read with no open-market buyer or seller in the window", () => {
    const { container } = setup("tr", <InsidersChip s={{ days: 90, buyers: 0, sellers: 0, net_value: 0, cluster: false }} />)
    expect(container.textContent).toBe("")
  })
})
