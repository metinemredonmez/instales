import { render, screen, within } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest"
import type { Ownership as OwnershipData } from "@/lib/api"
import { LangProvider } from "@/lib/i18n"
import { setLocale } from "@/lib/format"

const ownership = vi.fn<(market: string, symbol: string) => Promise<OwnershipData>>()
vi.mock("@/lib/api", () => ({ api: { ownership: (m: string, s: string) => ownership(m, s) } }))

import { CrowdingChip, Ownership, whyRows, levelOf } from "./Ownership"

/** Two of twelve holders listed (the page asked for the largest), two more too stale to list; one fund with a full row, one with the nullable fields null. */
const DATA: OwnershipData = {
  symbol: "ASELS", name: "Aselsan", market: "TR", as_of: "2026-09-15", shares_outstanding: 4_560_000_000, shares_as_of: "2026-06-30", currency: "TRY",
  totals: { holders: 12, institutions: 5, quantity: 146_000_000, market_value: 21_900_000_000, pct_of_shares: 3.2, top10_pct_of_held: 88.4, hhi: 1450 },
  // The engine's own why: four components with raw · normalized · weight · contribution (plus the odd extra figure), and stale_holders as a plain number.
  crowding: {
    score: 72, level: "high",
    why: {
      holders: { raw: 12, normalized: 0.6928, weight: 0.35, contribution: 24.25, saturation: 25 },
      held_pct: { raw: 3.2, normalized: 0.3266, weight: 0.25, contribution: 8.16, saturation: 30 },
      concentration: { raw: 1450, normalized: 0.855, weight: 0.2, contribution: 17.1, top10_pct_of_held: 88.4 },
      momentum: { raw: 4, normalized: 0.8944, weight: 0.2, contribution: 17.89, funds_increasing: 5, funds_reducing: 1, saturation: 5, window_days: 30 },
      stale_holders: 2,
    },
  },
  holders: [
    { fund: "TMV", name: "Örnek Fon", institution: "İş Portföy", quantity: 80_000_000, market_value: 12_000_000_000, weight_pct: 4.2, pct_of_shares: 1.75, as_of: "2026-09-15", last_move: "ADD", last_move_period_end: "2026-09-15", confidence: "INFERRED" },
    { fund: "MAC", name: "Mavi Fon", institution: "Ak Portföy", quantity: 66_000_000, market_value: null, weight_pct: null, pct_of_shares: 1.45, as_of: "2026-09-10", last_move: null, last_move_period_end: null, confidence: "EXACT" },
  ],
  stale_holders: 2,
}

function setup(lang: "tr" | "en" = "tr", ui: React.ReactNode = <Ownership symbol="ASELS" market="TR" />) {
  localStorage.setItem("instilens.lang", lang)
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  const tree = (inner: React.ReactNode) => (
    <QueryClientProvider client={qc}>
      <LangProvider><MemoryRouter>{inner}</MemoryRouter></LangProvider>
    </QueryClientProvider>
  )
  const utils = render(tree(ui))
  // Re-render with new props under the same providers — the stock page changes the symbol prop without remounting.
  return { user: userEvent.setup(), qc, ...utils, rerender: (inner: React.ReactNode) => utils.rerender(tree(inner)) }
}

/** The totals strip is the block under the "latest report · date" line; the table repeats some of its labels as column heads. */
const strip = () => within(screen.getByText(/^en son rapor · |^latest report · /).parentElement!)
const chip = (label: string) => strip().getByText(label).nextElementSibling!
const cells = (fund: string) => within(screen.getByRole("link", { name: fund }).closest("tr")!).getAllByRole("cell").map((c) => c.textContent)

describe("Ownership", () => {
  beforeEach(() => { ownership.mockReset(); ownership.mockResolvedValue(DATA) })
  afterEach(() => { localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("renders the totals strip from the API's figures and the crowding pill with its level", async () => {
    setup()
    expect(await screen.findByText("Kimler tutuyor")).toBeInTheDocument()
    expect(ownership).toHaveBeenLastCalledWith("TR", "ASELS")
    expect(screen.getByText("en son rapor · 15 Eyl 2026")).toBeInTheDocument()
    expect(chip("Tutan").textContent).toBe("12 fon · 5 kurum")
    expect(chip("Toplam lot").textContent).toBe("146.000.000")
    expect(chip("Piyasa değeri").textContent).toBe("₺21,9 mr")
    expect(chip("Dolaşımdaki payın").textContent).toBe("3,20%")
    expect(chip("Dolaşımdaki payın").parentElement).toHaveAttribute("title", "pay sayısı 30 Haz 2026")
    expect(chip("İlk 10'un payı").textContent).toBe("88,4%")
    expect(chip("HHI").textContent).toBe("1.450")
    // The section header and the crowding card show the same pill: score, then the level in words.
    expect(screen.getAllByText("· yüksek kalabalıklaşma")).toHaveLength(2)
    expect(screen.getAllByText("72")).toHaveLength(2)
    expect(screen.getByText("Kaynak: fon portföy raporları (KAP) / 13F · en son rapor · tavsiye değildir")).toBeInTheDocument()
  })

  it("lists one row per fund with lot, value, weights, last move and lineage — dashes where the API sent null", async () => {
    setup()
    await screen.findByText("Kimler tutuyor")
    expect(screen.getByRole("link", { name: "TMV" })).toHaveAttribute("href", "/funds/TMV")
    expect(cells("TMV")).toEqual(["TMVÖrnek Fon", "İş Portföy", "80.000.000", "₺12,0 mr", "4,2%", "1,75%", "ADD15 Eyl 2026", "INFERRED", "15 Eyl 2026"])
    expect(cells("MAC")).toEqual(["MACMavi Fon", "Ak Portföy", "66.000.000", "—", "—", "1,45%", "—", "EXACT", "10 Eyl 2026"])
    expect(screen.getByText("en büyük 2 fon listelendi · 2 fonun verisi güncel değil, listelenmedi")).toBeInTheDocument()
  })

  it("says the share count is unknown instead of a percentage when shares outstanding are null", async () => {
    ownership.mockResolvedValue({ ...DATA, shares_outstanding: null, shares_as_of: null, totals: { ...DATA.totals, pct_of_shares: null }, holders: DATA.holders.map((h) => ({ ...h, pct_of_shares: null })) })
    setup()
    await screen.findByText("Kimler tutuyor")
    expect(chip("Dolaşımdaki payın").textContent).toBe("pay sayısı bilinmiyor")
    expect(chip("Dolaşımdaki payın").parentElement).not.toHaveAttribute("title")
    expect(cells("TMV")[5]).toBe("—")
  })

  it("opens the why list: one row per component with its raw value, signed contribution and weight; stale_holders is not a row", async () => {
    const { user } = setup()
    await screen.findByText("Kimler tutuyor")
    expect(screen.queryByText("Tutan fon sayısı")).toBeNull()
    await user.click(screen.getByRole("button", { name: "Neden 72?" }))
    expect(screen.getByText("bileşen · ham değer · skora katkısı · ağırlık")).toBeInTheDocument()
    const list = within(screen.getByRole("list"))
    const row = (label: string) => list.getByText(label).nextElementSibling!.textContent
    expect(row("Tutan fon sayısı")).toBe("12 · +24,3 · ağırlık 35")
    expect(row("Tutulan payın yüzdesi")).toBe("3,2 · +8,2 · ağırlık 25")
    expect(row("Yoğunlaşma (HHI)")).toBe("1.450 · +17,1 · ağırlık 20")
    expect(row("Genişlik momentumu (artıran − azaltan fon)")).toBe("4 · +17,9 · ağırlık 20")
    expect(list.getAllByRole("listitem")).toHaveLength(4)
    expect(list.queryByText(/stale/)).toBeNull()
  })

  it("marks the held_pct component the engine skipped without a share count, with the rescaled weights", async () => {
    ownership.mockResolvedValue({
      ...DATA, shares_outstanding: null, shares_as_of: null, totals: { ...DATA.totals, pct_of_shares: null },
      crowding: {
        score: 74, level: "high",
        why: {
          holders: { raw: 12, normalized: 0.6928, weight: 0.4667, contribution: 32.33 },
          held_pct: { raw: null, normalized: null, weight: 0, contribution: 0, skipped: "shares outstanding unknown — held_pct skipped, its weight spread over the other components" },
          concentration: { raw: 1450, normalized: 0.855, weight: 0.2667, contribution: 22.8 },
          momentum: { raw: 4, normalized: 0.8944, weight: 0.2667, contribution: 23.85 },
          stale_holders: 2,
        },
      },
    })
    const { user } = setup()
    await screen.findByText("Kimler tutuyor")
    await user.click(screen.getByRole("button", { name: "Neden 74?" }))
    const list = within(screen.getByRole("list"))
    expect(list.getByText("Tutulan payın yüzdesi").nextElementSibling!.textContent).toBe("pay sayısı bilinmiyor · 0,0 · ağırlık 0")
    expect(list.getByText("Tutan fon sayısı").nextElementSibling!.textContent).toBe("12 · +32,3 · ağırlık 47")
  })

  it("names the reports that state no value in the market value tooltip", async () => {
    ownership.mockResolvedValue({ ...DATA, totals: { ...DATA.totals, unvalued_holders: 3 } })
    setup()
    await screen.findByText("Kimler tutuyor")
    expect(chip("Piyasa değeri").parentElement).toHaveAttribute("title", "3 fonun raporunda değer yok; toplama dahil değil")
  })

  it("renders no pill or why toggle when the crowding score has not been computed", async () => {
    ownership.mockResolvedValue({ ...DATA, crowding: null })
    setup()
    await screen.findByText("Kimler tutuyor")
    expect(screen.queryByText(/kalabalıklaşma/)).toBeNull()
    expect(screen.queryByRole("button", { name: /Neden/ })).toBeNull()
  })

  it("shows the empty state, with the stale count, when no current report holds the stock", async () => {
    ownership.mockResolvedValue({ ...DATA, holders: [], crowding: null, totals: { ...DATA.totals, holders: 0, institutions: 0, quantity: 0, market_value: null, pct_of_shares: null, top10_pct_of_held: null, hhi: null }, stale_holders: 3 })
    setup()
    expect(await screen.findByText("Bu hisseyi tutan güncel fon raporu yok.")).toBeInTheDocument()
    expect(screen.getByText("3 fonun verisi güncel değil, listelenmedi")).toBeInTheDocument()
    expect(screen.queryByRole("table")).toBeNull()
  })

  it("renders nothing at all when the request fails before anything loaded", async () => {
    ownership.mockRejectedValue(new Error("500 boom"))
    const { container } = setup()
    await vi.waitFor(() => expect(ownership).toHaveBeenCalled())
    await vi.waitFor(() => expect(container).toBeEmptyDOMElement())
  })

  it("keeps the table through a failed refetch of the same stock, but never shows it under another symbol whose load failed", async () => {
    const { qc, rerender } = setup()
    await screen.findByText("Kimler tutuyor")
    expect(screen.getByRole("link", { name: "TMV" })).toBeInTheDocument()
    ownership.mockRejectedValue(new Error("500 boom"))
    await qc.refetchQueries({ queryKey: ["ownership", "TR", "ASELS"] })
    expect(await screen.findByText("Sahiplik verisi yüklenemedi.")).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "TMV" })).toBeInTheDocument()   // same stock: last good payload stays
    rerender(<Ownership symbol="THYAO" market="TR" />)
    await vi.waitFor(() => expect(ownership).toHaveBeenLastCalledWith("TR", "THYAO"))
    await vi.waitFor(() => expect(screen.queryByRole("link", { name: "TMV" })).toBeNull())   // another stock: ASELS's holders are not THYAO's
    expect(screen.queryByText("Kimler tutuyor")).toBeNull()
  })

  it("speaks English", async () => {
    setup("en")
    expect(await screen.findByText("Who holds it")).toBeInTheDocument()
    expect(chip("Holders").textContent).toBe("12 funds · 5 institutions")
    expect(chip("Of shares outstanding").textContent).toBe("3.20%")
    expect(screen.getAllByText("· high crowding")).toHaveLength(2)
    expect(screen.getByText("largest 2 funds listed · 2 funds' data is not current and is not listed")).toBeInTheDocument()
    expect(screen.getByText("Source: fund portfolio reports (KAP) / 13F · latest report · not advice")).toBeInTheDocument()
  })
})

describe("CrowdingChip", () => {
  afterEach(() => { localStorage.removeItem("instilens.lang"); setLocale("tr") })

  it("reads the stored score row: label, score and a level from the thresholds when the row carries none", () => {
    setup("tr", <CrowdingChip s={{ score: 40, why: {} }} />)
    expect(screen.getByText("Kalabalıklaşma")).toBeInTheDocument()
    expect(screen.getByText("40")).toBeInTheDocument()
    expect(screen.getByText("· orta kalabalıklaşma")).toBeInTheDocument()
    expect(screen.getByTitle(/yoğunlaşma \(HHI\) ve genişlik momentumundan/)).toBeInTheDocument()
  })

  it("prefers the row's own level, then one written into why, then the thresholds", () => {
    expect(levelOf({ score: 80, level: "medium", why: {} })).toBe("medium")
    expect(levelOf({ score: 80, why: { level: "low" } })).toBe("low")
    expect(levelOf({ score: 80, why: {} })).toBe("high")
    expect(levelOf({ score: 35, why: {} })).toBe("medium")
    expect(levelOf({ score: 34.9, why: {} })).toBe("low")
    expect(levelOf({ score: 65, why: {} })).toBe("medium")
  })
})

describe("whyRows", () => {
  it("keeps the objects that carry a contribution, skipping bare numbers, strings, nulls and tables", () => {
    expect(whyRows({ holders: { raw: 12, weight: 0.35, contribution: 18.5 }, stale_holders: 2, level: "high", weights: { holders: 0.25 }, note: null })).toEqual([
      { key: "holders", raw: 12, contribution: 18.5, weight: 0.35, skipped: false },
    ])
    expect(whyRows({ momentum: { value: 4, contribution: 10 }, held_pct: { raw: null, contribution: 0, skipped: "unknown" } })).toEqual([
      { key: "momentum", raw: 4, contribution: 10, weight: null, skipped: false },
      { key: "held_pct", raw: null, contribution: 0, weight: null, skipped: true },
    ])
  })
})
