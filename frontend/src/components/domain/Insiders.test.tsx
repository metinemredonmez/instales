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
  code: "P", acquired: true, shares: 1_200, price: 187.5, price_range: null, value: 225_000, post_shares: 41_200, ownership: "D", derivative: false, confidence: "EXACT",
  accession: `0001234567-26-${String(id).padStart(6, "0")}`, url: `${EDGAR}/0001234567-26-${String(id).padStart(6, "0")}-index.htm`,
  party_kind: null, buyback: false, post_pct_stake: null,
  ...over,
})

/** A purchase, a sale, a grant without a price and a derivative exercise — the four shapes the table distinguishes. */
const DATA: InsidersData = {
  symbol: "AAPL", name: "Apple Inc.", market: "US", supported: true, days: 90, as_of: "2026-09-16T12:00:00Z", source: "sec-edgar", fetched_at: "2026-09-16T05:30:00Z", coverage_since: null,
  truncated: false, edgar_url: EDGAR_FORM4S, more_url: EDGAR_FORM4S,
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

  it("hides itself where the API answers supported:false", async () => {
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

const KAP = "https://www.kap.org.tr/tr/Bildirim"
const ktx = (id: number, index: number, over: Partial<InsiderTx>): InsiderTx => ({
  id, transaction_date: "2026-09-10", filed_at: "2026-09-10", insider: "Ayşe Yılmaz", insider_cik: "", role: "director", title: null,
  code: "P", acquired: true, shares: 100_000, price: 60, price_range: null, value: 6_000_000, post_shares: null, ownership: "D", derivative: false, confidence: "EXACT",
  accession: String(index), url: `${KAP}/${index}`, party_kind: "person", buyback: false, post_pct_stake: null,
  ...over,
})

/**
 * A BIST issuer, shaped as the KAP path stores it (`post_shares` is never filled — the filing states a stake, not a
 * count; the stake keeps its four decimals): a director's buy with the stake the filing states, a shareholder's sell,
 * the controlling holding company's buy filed with a price range only (a shareholder's purchase — counted,
 * `party_kind` company, not a buyback), the company's own buyback (`buyback` true, role "issuer", acquired) and its
 * treasury-share sale (`buyback` true, disposed) — both listed, neither summed. `coverage_since` is the day the KAP
 * feed's oldest stored filing was published; `more_url` the issuer's own KAP page.
 */
const KAP_ISSUER_PAGE = "https://www.kap.org.tr/tr/sirket-bilgileri/ozet/4028e4a241558bd9014155dde60a05e7"
const KAP_DATA: InsidersData = {
  symbol: "ASELS", name: "Aselsan", market: "TR", supported: true, days: 90, as_of: "2026-09-16T12:00:00Z", source: "kap", fetched_at: "2026-09-16T09:30:00Z", coverage_since: "2026-05-01",
  truncated: false, edgar_url: null, more_url: KAP_ISSUER_PAGE,
  summary: { buyers: 3, sellers: 1, buy_value: 6_000_000, sell_value: 1_600_000, net_value: 4_400_000, open_market_buys: 3, open_market_sells: 1, cluster: { since: "2026-08-17", insiders: 3, value: 6_000_000 } },
  transactions: [
    ktx(1, 1500001, { post_pct_stake: 5.12 }),
    ktx(2, 1500002, { insider: "Mehmet Kaya", role: "shareholder", code: "S", acquired: false, shares: 20_000, price: 80, value: 1_600_000, post_pct_stake: 0.0758, transaction_date: "2026-09-08", filed_at: "2026-09-09" }),
    ktx(3, 1500003, { insider: "Akkök Holding A.Ş.", role: "shareholder", party_kind: "company", shares: 1_750_000, price: null, price_range: [10.41, 10.69], value: null, post_pct_stake: 40.22, transaction_date: "2026-09-06", filed_at: "2026-09-07" }),
    ktx(4, 1500004, { insider: "Aselsan Elektronik Sanayi ve Ticaret A.Ş.", role: "issuer", party_kind: "company", buyback: true, shares: 50_000, price: 58, value: 2_900_000, post_pct_stake: 0.4, transaction_date: "2026-09-05", filed_at: "2026-09-05" }),
    ktx(5, 1500005, { insider: "Aselsan Elektronik Sanayi ve Ticaret A.Ş.", role: "issuer", party_kind: "company", buyback: true, code: "S", acquired: false, shares: 30_000, price: 61, value: 1_830_000, post_pct_stake: 0.38, transaction_date: "2026-09-04", filed_at: "2026-09-04" }),
  ],
}

describe("Insiders on BIST (KAP)", () => {
  beforeEach(() => { insiders.mockReset(); insiders.mockResolvedValue(KAP_DATA) })
  afterEach(() => { localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("renders the section with the KAP hint, lira figures, the KAP wording of buys and sells and a link to each disclosure", async () => {
    setup("tr", <Insiders symbol="ASELS" market="TR" />)
    expect(await screen.findByText("İçeriden işlemler")).toBeInTheDocument()
    expect(insiders).toHaveBeenLastCalledWith("TR", "ASELS", 90)
    expect(screen.getByText("KAP pay alım satım bildirimleri · yönetim kurulu, yönetici ve ortak işlemleri")).toBeInTheDocument()
    const chip = (label: string) => screen.getByText(label).nextElementSibling!
    expect(chip("Alıcı").textContent).toBe("3 kişi")
    expect(chip("Net değer").textContent).toBe("+₺4,4 mn")
    expect(chip("Alış/satış").textContent).toBe("3 alış · 1 satış")
    expect(screen.queryByText("Açık piyasa")).toBeNull()
    expect(screen.getByText(/^▲ Küme: 30 günde 3 şirket içi kişi pay aldı ·/).textContent).toBe("▲ Küme: 30 günde 3 şirket içi kişi pay aldı · ₺6,0 mn")
    // The stake stands alone in the post-transaction cell (a KAP filing states no share count), with the four decimals the
    // form carries and no trailing zeros; the ownership cell is "—": the filing states no direct / indirect nature.
    expect(cells("Ayşe Yılmaz")).toEqual(["10 Eyl 2026bildirim 10 Eyl 2026", "Ayşe YılmazYönetim kurulu üyesi", "PAlış", "+100.000", "₺60,00", "+₺6,0 mn", "%5,12", "—", ""])
    expect(cells("Mehmet Kaya")).toEqual(["08 Eyl 2026bildirim 09 Eyl 2026", "Mehmet KayaPay sahibi", "SSatış", "-20.000", "₺80,00", "-₺1,6 mn", "%0,0758", "—", ""])   // "Pay sahibi": the filing's word, no stake claimed
    // A range is printed as filed, low–high, and the value stays empty: no midpoint is ever computed.
    expect(cells("Akkök Holding A.Ş.")).toEqual(["06 Eyl 2026bildirim 07 Eyl 2026", "Akkök Holding A.Ş.Pay sahibi", "PAlışTüzel kişi", "+1.750.000", "₺10,41–10,69", "—", "%40,22", "—", ""])
    expect(screen.queryByText("Doğrudan")).toBeNull()
    expect(screen.queryByText(/%5\+/)).toBeNull()
    const link = rowOf("Ayşe Yılmaz").getByRole("link", { name: "KAP'ta aç" })
    expect(link).toHaveAttribute("href", `${KAP}/1500001`)
    expect(link).toHaveAttribute("target", "_blank")
    expect(screen.queryByRole("link", { name: "EDGAR'da aç" })).toBeNull()
    expect(screen.getByText("Kaynak: KAP · rakamlar bildirimde yazıldığı gibi · tavsiye değildir")).toBeInTheDocument()
  })

  it("keeps the company's own-share trades in the list under neutral chips — a buyback when it bought, a treasury-share sale when it sold — uncoloured, and says the summary leaves them out", async () => {
    setup("tr", <Insiders symbol="ASELS" market="TR" />)
    await screen.findByRole("table")
    const [buyback, sale] = screen.getAllByText("Aselsan Elektronik Sanayi ve Ticaret A.Ş.").map((el) => within(el.closest("tr")!))
    expect(buyback.getByText("Şirket geri alımı")).toBeInTheDocument()
    const c = buyback.getAllByRole("cell")
    expect(c[1].textContent).toBe("Aselsan Elektronik Sanayi ve Ticaret A.Ş.İhraççı şirket")   // the role token "issuer" is labelled, never printed raw
    expect(c[2].textContent).toBe("PAlışŞirket geri alımı")
    expect(c[2]).not.toHaveClass("text-positive")
    expect(c[3]).not.toHaveClass("text-positive")
    expect(c[5].textContent).toBe("+₺2,9 mn")
    expect(c[5]).not.toHaveClass("text-positive")
    // The company SELLING its own shares is never called a buyback: the chip says what the filing says.
    const sc = sale.getAllByRole("cell")
    expect(sc[2].textContent).toBe("SSatışŞirketin kendi pay satışı")
    expect(sc[2]).not.toHaveClass("text-negative")
    expect(sc[3].textContent).toBe("-30.000")
    expect(sc[5].textContent).toBe("-₺1,8 mn")
    expect(sc[5]).not.toHaveClass("text-negative")
    expect(sale.queryByText("Şirket geri alımı")).toBeNull()
    expect(rowOf("Ayşe Yılmaz").getAllByRole("cell")[2]).toHaveClass("text-positive")   // a person's buy keeps its colour
    expect(screen.getAllByText("Şirket geri alımı")).toHaveLength(1)
    expect(screen.getByText("Son 90 gün · 16 Eyl 2026 · şirketin kendi pay işlemleri özete dahil değil")).toBeInTheDocument()
  })

  it("tells a controlling company's purchase apart from a buyback: party_kind alone is not the test, the API's buyback flag is", async () => {
    setup("tr", <Insiders symbol="ASELS" market="TR" />)
    await screen.findByRole("table")
    const holding = rowOf("Akkök Holding A.Ş.").getAllByRole("cell")
    expect(holding[2].textContent).toBe("PAlışTüzel kişi")
    expect(holding[2]).toHaveClass("text-positive")   // a shareholder's purchase: coloured like any insider buy
    expect(holding[3]).toHaveClass("text-positive")
    expect(rowOf("Akkök Holding A.Ş.").queryByText("Şirket geri alımı")).toBeNull()
  })

  it("leaves the own-share note out when no row is the company's own", async () => {
    insiders.mockResolvedValue({ ...KAP_DATA, transactions: KAP_DATA.transactions.slice(0, 3) })
    setup("tr", <Insiders symbol="ASELS" market="TR" />)
    await screen.findByRole("table")
    expect(screen.getByText("Son 90 gün · 16 Eyl 2026")).toBeInTheDocument()
    expect(screen.queryByText(/kendi pay işlemleri özete dahil değil/)).toBeNull()
  })

  it("says the KAP filings have not been read yet, in KAP words and market-wide (the feed is not per issuer)", async () => {
    insiders.mockResolvedValue({ ...KAP_DATA, fetched_at: null, coverage_since: null, summary: EMPTY.summary, transactions: [] })
    setup("tr", <Insiders symbol="ASELS" market="TR" />)
    expect(await screen.findByText("KAP pay alım satım bildirimleri henüz okunmadı; iş çalışınca burada listelenir.")).toBeInTheDocument()
    expect(screen.queryByText(/Form 4/)).toBeNull()
    expect(screen.queryByText(/Bu şirketin/)).toBeNull()
  })

  it("an empty window that starts before the feed's coverage says 'nothing since that day', never 'nothing in 90 days'", async () => {
    // The feed lists a week per run: the day after its first run, coverage_since is 2026-09-09 and a 90-day window is mostly unread.
    insiders.mockResolvedValue({ ...KAP_DATA, coverage_since: "2026-09-09", summary: EMPTY.summary, transactions: [] })
    const { user } = setup("tr", <Insiders symbol="ASELS" market="TR" />)
    expect(await screen.findByText("09 Eyl 2026 tarihinden beri raporlanmış içeriden işlem yok (KAP bildirimleri o günden itibaren okundu).")).toBeInTheDocument()
    expect(screen.queryByText(/Son 90 günde/)).toBeNull()
    await user.click(screen.getByRole("button", { name: "1Y" }))
    await waitFor(() => expect(insiders).toHaveBeenLastCalledWith("TR", "ASELS", 365))
    expect(await screen.findByText(/09 Eyl 2026 tarihinden beri/)).toBeInTheDocument()
    // Once the feed has read the whole window (coverage_since before as_of − days), the plain window wording is true again.
    insiders.mockResolvedValue({ ...KAP_DATA, days: 90, coverage_since: "2026-05-01", summary: EMPTY.summary, transactions: [] })
    await user.click(screen.getByRole("button", { name: "90G" }))
    expect(await screen.findByText("Son 90 günde raporlanmış içeriden işlem yok.")).toBeInTheDocument()
  })

  it("links the truncation note to the issuer's own KAP page when the API knows it", async () => {
    const many = Array.from({ length: 200 }, (_, i) => ktx(100 + i, 1600000 + i, { insider: `Kişi ${i}` }))
    insiders.mockResolvedValue({ ...KAP_DATA, transactions: many, truncated: true })
    setup("tr", <Insiders symbol="ASELS" market="TR" />)
    await screen.findByRole("table")
    const note = screen.getByRole("link", { name: "İlk 200 işlem · gerisi KAP'ta" })
    expect(note).toHaveAttribute("href", KAP_ISSUER_PAGE)
    expect(note).toHaveAttribute("target", "_blank")
  })

  it("prints the truncation note plain when the issuer's KAP page is not known (only MKK-relayed filings stored)", async () => {
    const many = Array.from({ length: 200 }, (_, i) => ktx(100 + i, 1600000 + i, { insider: `Kişi ${i}` }))
    insiders.mockResolvedValue({ ...KAP_DATA, transactions: many, truncated: true, more_url: null })
    setup("tr", <Insiders symbol="BURVA" market="TR" />)
    await screen.findByRole("table")
    expect(screen.getByText("İlk 200 işlem · gerisi KAP'ta")).toBeInTheDocument()
    expect(screen.queryByRole("link", { name: "İlk 200 işlem · gerisi KAP'ta" })).toBeNull()
  })

  it("renders English KAP labels", async () => {
    setup("en", <Insiders symbol="ASELS" market="TR" />)
    expect(await screen.findByText("Insider transactions")).toBeInTheDocument()
    expect(screen.getByText("KAP share purchase/sale filings · board member, executive and shareholder trades")).toBeInTheDocument()
    expect(cells("Ayşe Yılmaz")).toEqual(["10 Sept 2026filed 10 Sept 2026", "Ayşe YılmazDirector", "PPurchase", "+100,000", "₺60.00", "+₺6.0 mn", "5.12%", "—", ""])
    expect(rowOf("Mehmet Kaya").getByText("Shareholder")).toBeInTheDocument()
    expect(cells("Mehmet Kaya")[6]).toBe("0.0758%")
    expect(screen.getByText("Company buyback")).toBeInTheDocument()
    expect(screen.getByText("Treasury-share sale")).toBeInTheDocument()
    expect(screen.getByText("Legal entity")).toBeInTheDocument()
    expect(screen.getAllByText("Issuer")).toHaveLength(2)
    expect(cells("Akkök Holding A.Ş.").slice(4, 6)).toEqual(["₺10.41–10.69", "—"])
    expect(screen.getByText("Last 90 days · 16 Sept 2026 · the company's own-share trades are not part of the summary")).toBeInTheDocument()
    expect(screen.getAllByRole("link", { name: "Open on KAP" })).toHaveLength(5)
    expect(screen.getByText("Source: KAP · figures as stated in the filing · not advice")).toBeInTheDocument()
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
