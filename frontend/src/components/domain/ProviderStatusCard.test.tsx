import { render, screen, within } from "@testing-library/react"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { ProvidersStatus } from "@/lib/api"

const adminProviders = vi.fn<() => Promise<ProvidersStatus>>()
vi.mock("@/lib/api", () => ({ api: { adminProviders: () => adminProviders() } }))

import { ProviderStatusCard } from "./ProviderStatusCard"

const YAHOO: ProvidersStatus = {
  price: {
    active: "yahoo", selected: "yahoo", available: ["yahoo", "matriks"],
    status: { name: "yahoo", configured: true, connected: true, delay: "delayed", last_tick_at: "2026-09-17T09:00:00Z", error: null, note: null },
    status_from: "feed",
  },
  feed: { running: true, last_run_at: "2026-09-17T09:00:05Z", interval_s: 60, published: 1234, provider: "yahoo", error: null },
}

/** The admin picked matriks without its keys: yahoo answers, and the feed's last iteration failed on its own (not the provider). */
const FALLBACK: ProvidersStatus = {
  price: {
    active: "yahoo", selected: "matriks", available: ["yahoo", "matriks"],
    status: { name: "yahoo", configured: true, connected: true, delay: "delayed", last_tick_at: "2026-09-17T09:00:00Z", error: null, note: null },
  },
  feed: { running: true, last_run_at: "2026-09-17T09:00:05Z", interval_s: 60, published: 3, provider: "yahoo", error: "OperationalError: database is locked" },
}

const MATRIKS: ProvidersStatus = {
  price: {
    active: "matriks", selected: "matriks", available: ["yahoo", "matriks"],
    status: { name: "matriks", configured: false, connected: false, delay: "realtime", last_tick_at: null, error: null, note: "awaiting vendor documentation" },
    status_from: "api",
  },
  feed: { running: false, last_run_at: null, interval_s: 60, published: 0, provider: null, error: null },
}

/** The three Matriks keys are set, so the slot is selected — and refuses every call; the refusal is its error, relayed by the feed too. */
const MATRIKS_CONFIGURED: ProvidersStatus = {
  price: {
    active: "matriks", selected: "matriks", available: ["yahoo", "matriks"],
    status: { name: "matriks", configured: true, connected: false, delay: "delayed", last_tick_at: null, error: "matriks: no adapter yet", note: "awaiting vendor documentation" },
    status_from: "feed",
  },
  feed: { running: true, last_run_at: "2026-09-17T09:00:05Z", interval_s: 60, published: 0, provider: "matriks", error: "matriks: no adapter yet" },
}

function setup() {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><ProviderStatusCard /></QueryClientProvider>)
}

describe("ProviderStatusCard", () => {
  beforeEach(() => { adminProviders.mockReset() })   // braces: a returned mock would run as a cleanup hook

  it("shows the active provider, its chips, last tick and the feed heartbeat", async () => {
    adminProviders.mockResolvedValue(YAHOO)
    setup()
    expect(await screen.findByText("Yahoo Finance")).toBeInTheDocument()   // label from the active key
    expect(screen.getByText("yahoo")).toBeInTheDocument()                    // the provider's own name
    expect(screen.getByText("yahoo, matriks")).toBeInTheDocument()
    expect(screen.getByText("Tanımlı")).toBeInTheDocument()
    expect(screen.getByText("Bağlı")).toBeInTheDocument()
    expect(screen.getByText("Gecikmeli")).toBeInTheDocument()
    expect(screen.getByText("Çalışıyor")).toBeInTheDocument()
    expect(screen.getByText("60 sn")).toBeInTheDocument()
    expect(screen.getByText("1234")).toBeInTheDocument()
    expect(screen.queryByText("Hata")).toBeNull()      // no error row when the adapter reported none
    expect(screen.queryByText("Not")).toBeNull()
    expect(screen.getByText("· feed sürecinin gördüğü")).toBeInTheDocument()   // whose instance the report is
    expect(screen.getByText("Gecikmeli").className).toContain("text-warning")  // a connected provider's delay is a fact, coloured as one
    expect(adminProviders).toHaveBeenCalledTimes(1)
  })

  it("shows a configured-but-refusing slot with its error, no positive delay colour and no fallback hint", async () => {
    adminProviders.mockResolvedValue(MATRIKS_CONFIGURED)
    setup()
    expect(await screen.findByText("Matriks")).toBeInTheDocument()
    expect(screen.getByText("Tanımlı")).toBeInTheDocument()
    expect(screen.getByText("Bağlı değil")).toBeInTheDocument()
    expect(screen.getByText("Gecikmeli").className).toContain("text-muted-foreground")   // never connected: nothing to colour green
    expect(screen.getByText("matriks: no adapter yet")).toBeInTheDocument()
    expect(screen.getAllByText("Hata")).toHaveLength(1)   // the heartbeat relays the same refusal: one row, not two
    expect(screen.queryByText(/yedek sağlayıcı/)).toBeNull()
  })

  it("reports an unconfigured provider and a stopped feed honestly, with the adapter's note", async () => {
    adminProviders.mockResolvedValue(MATRIKS)
    setup()
    expect(await screen.findByText("Matriks")).toBeInTheDocument()
    expect(screen.getByText("Tanımsız")).toBeInTheDocument()
    expect(screen.getByText("Bağlı değil")).toBeInTheDocument()
    expect(screen.getByText("Gerçek zamanlı")).toBeInTheDocument()
    expect(screen.getByText("Gerçek zamanlı").className).toContain("text-muted-foreground")   // a claim no connection has backed
    expect(screen.getByText("· API sürecinin gördüğü (feed çalışmıyor)")).toBeInTheDocument()
    expect(screen.getByText("Çalışmıyor")).toBeInTheDocument()
    expect(screen.getByText("awaiting vendor documentation")).toBeInTheDocument()
    const region = screen.getByRole("heading", { name: "Veri sağlayıcıları" }).closest("section")!
    expect(within(region).getAllByText("—")).toHaveLength(2)   // no last tick, no last run
  })

  it("names the fallback when the chosen provider is not the one answering, and the feed's own error", async () => {
    adminProviders.mockResolvedValue(FALLBACK)
    setup()
    expect(await screen.findByText("Yahoo Finance")).toBeInTheDocument()
    expect(screen.getByText("seçilen Matriks yapılandırılmadı, yedek sağlayıcı yanıtlıyor")).toBeInTheDocument()   // the label, not the config key
    expect(screen.queryByText(/sürecinin gördüğü/)).toBeNull()   // an API that does not say whose report it is gets no head suffix
    expect(screen.getByText("OperationalError: database is locked")).toBeInTheDocument()
    expect(screen.getAllByText("Hata")).toHaveLength(1)   // the feed row only — the provider reported none
  })

  it("does not repeat the provider's error as the feed's when the heartbeat merely relays it", async () => {
    const outage = "HTTPError: 503"
    adminProviders.mockResolvedValue({ ...YAHOO, price: { ...YAHOO.price, status: { ...YAHOO.price.status, connected: false, error: outage } }, feed: { ...YAHOO.feed, error: outage } })
    setup()
    expect(await screen.findByText(outage)).toBeInTheDocument()
    expect(screen.getAllByText("Hata")).toHaveLength(1)
    expect(screen.queryByText(/yedek sağlayıcı/)).toBeNull()   // selected === active: no fallback hint
  })

  it("says so when the status endpoint fails instead of showing a blank card", async () => {
    adminProviders.mockRejectedValue(new Error("503"))
    setup()
    expect(await screen.findByText("Sağlayıcı durumu alınamadı.")).toBeInTheDocument()
  })
})
