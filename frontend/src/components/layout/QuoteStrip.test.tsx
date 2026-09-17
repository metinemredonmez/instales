import { render, screen } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { QuotesResponse } from "@/lib/api"

const quotes = vi.fn<() => Promise<QuotesResponse>>()
vi.mock("@/lib/api", () => ({ api: { quotes: () => quotes() } }))

import { QuoteStrip } from "./QuoteStrip"

const DATA: QuotesResponse = {
  as_of: "2026-09-17T09:00:05Z",
  quotes: [
    { key: "USDTRY", label: "USD/TRY", price: 41.2345, change_pct: 0.12, currency: "TRY", updated_at: "2026-09-17T09:00:00Z", decimals: 4, bar_date: "2026-09-17", source: "yahoo", delayed: true },
    { key: "XU100", label: "BIST 100", price: 10482.6, change_pct: -0.4, currency: "TRY", updated_at: "2026-09-17T09:00:00Z", decimals: 0, bar_date: "2026-09-17", source: "yahoo", delayed: true },
  ],
  markets: { TR: { state: "open", next_change_at: "2026-09-17T15:00:00Z", tz: "Europe/Istanbul" } },
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><QuoteStrip /></QueryClientProvider>)
}

describe("QuoteStrip source tooltip", () => {
  beforeEach(() => { quotes.mockReset(); quotes.mockResolvedValue(DATA) })

  it("names the provider and its delay for a delayed Yahoo payload", async () => {
    setup()
    const list = await screen.findByRole("list", { name: "Piyasa özeti" })
    expect(list).toHaveAttribute("title", "Kaynak: Yahoo Finance · ~15 dk gecikmeli")
    expect(screen.getAllByRole("listitem")).toHaveLength(2)
  })

  it("renders nothing until the first answer arrives", () => {
    quotes.mockReturnValue(new Promise(() => {}))
    setup()
    expect(screen.queryByRole("list")).toBeNull()
  })
})
