import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { Freshness } from "@/lib/api"
import { translate } from "@/lib/i18n"

const freshness = vi.fn<(market: string) => Promise<Freshness[]>>()
vi.mock("@/lib/api", () => ({ api: { freshness: (m: string) => freshness(m) } }))

import { FreshnessBar, freshnessCadence, freshnessSource } from "./Freshness"

const TR: Freshness[] = [
  { source: "KAP transaction disclosures", cadence: "Same-day", last: "2026-09-17T08:10:00Z", delayed: false },
  { source: "KAP portfolio reports", cadence: "Monthly snapshot", last: "2026-08-31", delayed: true },
  { source: "KAP insider filings", cadence: "Every 30 min on trading days", last: null, delayed: false },
  { source: "Market prices", cadence: "Daily (yahoo, delayed)", last: "2026-09-16", delayed: false },
]

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><FreshnessBar market="TR" /></QueryClientProvider>)
}

describe("FreshnessBar", () => {
  beforeEach(() => { freshness.mockReset(); freshness.mockResolvedValue(TR) })

  it("shows the API's English source names in Turkish, with the cadence as the tooltip", async () => {
    setup()
    expect(await screen.findByText("KAP işlem bildirimleri")).toBeInTheDocument()
    expect(screen.getByText("KAP portföy raporları")).toBeInTheDocument()
    expect(screen.getByText("KAP içeriden işlemler")).toBeInTheDocument()
    const prices = screen.getByText("Piyasa fiyatları")
    expect(prices.closest("span[title]")).toHaveAttribute("title", "Günlük (Yahoo Finance, gecikmeli)")
    expect(screen.getByText("KAP işlem bildirimleri").closest("span[title]")).toHaveAttribute("title", "Aynı gün")
    expect(screen.queryByText("Market prices")).toBeNull()
  })

  it("keeps a source or cadence it does not know exactly as the API sent it", async () => {
    freshness.mockResolvedValue([{ source: "Something new", cadence: "Whenever", last: null, delayed: false }])
    setup()
    expect(await screen.findByText("Something new")).toBeInTheDocument()
    expect(screen.getByText("Something new").closest("span[title]")).toHaveAttribute("title", "Whenever")
  })
})

describe("freshness labels", () => {
  const t = (k: Parameters<typeof translate>[1], v?: Parameters<typeof translate>[2]) => translate("en", k, v)
  it("maps every source the API lists, in English too, and passes unknown ones through", () => {
    expect(freshnessSource("SEC 13F", t)).toBe("SEC 13F")
    expect(freshnessSource("SEC Form 4", t)).toBe("SEC Form 4")
    expect(freshnessSource("KAP insider filings", t)).toBe("KAP insider filings")
    expect(freshnessSource("Ledger of the future", t)).toBe("Ledger of the future")
  })
  it("translates the daily cadence's provider and delay word by word and leaves an unknown provider's name alone", () => {
    expect(freshnessCadence("Daily (yahoo, delayed)", t)).toBe("Daily (Yahoo Finance, delayed)")
    expect(freshnessCadence("Daily (matriks, realtime)", t)).toBe("Daily (Matriks, live)")
    expect(freshnessCadence("Daily (acme, eod)", t)).toBe("Daily (acme, end of day)")
    expect(freshnessCadence("Quarterly, up to 45 days after quarter end", t)).toBe("Quarterly, up to 45 days after quarter end")
  })
})
