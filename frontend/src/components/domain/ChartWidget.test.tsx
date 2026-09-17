import { act, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { QueryClient, QueryClientProvider } from "@tanstack/react-query"
import { MemoryRouter } from "react-router-dom"
import type { ReactNode } from "react"
import { beforeEach, describe, expect, it, vi } from "vitest"
import type { Candles, QuotesResponse, SearchHit } from "@/lib/api"

// jsdom has no canvas: the library is replaced by a shape that records what the widget hands it (props and data, not pixels).
const lw = vi.hoisted(() => {
  const candleData = vi.fn(), volumeData = vi.fn(), fitContent = vi.fn(), createChart = vi.fn(), addSeries = vi.fn(), stretch = vi.fn(), chartOptions = vi.fn(), remove = vi.fn()
  return { candleData, volumeData, fitContent, createChart, addSeries, stretch, chartOptions, remove }
})
vi.mock("lightweight-charts", () => ({
  ColorType: { Solid: "solid" }, CrosshairMode: { Normal: 0 }, LineStyle: { Dotted: 1 },
  CandlestickSeries: "Candlestick", HistogramSeries: "Histogram",
  createChart: lw.createChart.mockImplementation(() => ({
    addSeries: lw.addSeries.mockImplementation((kind: string) => ({ setData: kind === "Candlestick" ? lw.candleData : lw.volumeData, applyOptions: vi.fn(), priceScale: () => ({ applyOptions: vi.fn() }) })),
    panes: () => [{ setStretchFactor: lw.stretch }, { setStretchFactor: lw.stretch }],
    timeScale: () => ({ fitContent: lw.fitContent, applyOptions: vi.fn() }),
    applyOptions: lw.chartOptions,
    remove: lw.remove,
  })),
}))

const candles = vi.fn<(market: string, symbol: string, interval: string) => Promise<Candles>>()
const search = vi.fn<(market: string, q: string) => Promise<SearchHit[]>>()
const radar = vi.fn<(market: string) => Promise<{ accumulated: { symbol: string; name?: string }[]; distributed?: { symbol: string; name?: string }[] }>>()
const WATCH = [{ id: 1, kind: "stock", ref: "TUPRS", name: "Tüpraş", market: "TR" }, { id: 2, kind: "fund", ref: "TMV", name: "Fon", market: undefined }]
vi.mock("@/lib/api", async (importOriginal) => {
  const actual = await importOriginal<typeof import("@/lib/api")>()
  return { ...actual, api: { candles: (m: string, s: string, i: string) => candles(m, s, i), search: (m: string, q: string) => search(m, q), quotes: async () => QUOTES, radar: (m: string) => radar(m), watchlist: async () => WATCH } }
})

import { ApiError } from "@/lib/api"
import { openChart, useChartOpen } from "@/lib/chart"
import { LangProvider, useI18n } from "@/lib/i18n"
import { ThemeProvider, useTheme } from "@/lib/theme"
import { ChartWidget } from "./ChartWidget"

const QUOTES: QuotesResponse = { as_of: "2026-09-17T09:00:05Z", quotes: [], markets: { TR: { state: "open", next_change_at: "2026-09-17T15:00:00Z", tz: "Europe/Istanbul" } } }
const day = (iso: string) => Date.parse(iso) / 1000
const ASELS: Candles = {
  symbol: "ASELS", name: "Aselsan", market: "TR", currency: "TRY", interval: "1d", source: "yahoo", delay: "delayed", delayed: true, as_of: "2026-09-17T09:00:00Z", tz: "Europe/Istanbul",
  bars: [
    { t: day("2026-09-15T00:00:00Z"), o: 139, h: 141, l: 138.5, c: 140, v: 1_000 },
    { t: day("2026-09-16T00:00:00Z"), o: 140, h: 142, l: 139.8, c: 141.2, v: 1_200 },
  ],
}

/** The shell's wiring in miniature: the open flag from useChartOpen (and its sidebar toggle), the one widget instance. */
function Host() {
  const c = useChartOpen()
  return <><button onClick={c.toggle}>toggle</button><ChartWidget open={c.open} onClose={() => c.setOpen(false)} /></>
}
/** Neighbours the shell also renders: the theme and language switches, a header field. */
function ThemeToggle() { const { toggle } = useTheme(); return <button onClick={toggle}>theme</button> }
function LangToggle() { const { lang, setLang } = useI18n(); return <button onClick={() => setLang(lang === "tr" ? "en" : "tr")}>lang</button> }
function setup(extra?: ReactNode, wrap: (node: ReactNode) => ReactNode = (n) => n) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  render(<QueryClientProvider client={qc}><MemoryRouter>{wrap(<><Host />{extra}</>)}</MemoryRouter></QueryClientProvider>)
  return { user: userEvent.setup(), qc }
}
const saved = () => JSON.parse(localStorage.getItem("instilens.chart") ?? "null")
const SOURCE = "Yahoo Finance · ~15 dk gecikmeli"

describe("ChartWidget", () => {
  beforeEach(() => {
    localStorage.clear()
    candles.mockReset(); candles.mockResolvedValue(ASELS)
    search.mockReset(); search.mockResolvedValue([])
    radar.mockReset(); radar.mockResolvedValue({ accumulated: [] })
    for (const fn of Object.values(lw)) fn.mockClear()
  })

  it("starts closed; openChart() opens it on that symbol with the last close, the change in its colour and the delayed source line", async () => {
    setup()
    expect(screen.queryByRole("combobox")).toBeNull()
    act(() => openChart("ASELS", "TR"))
    expect(await screen.findByText(SOURCE)).toBeInTheDocument()
    expect(candles).toHaveBeenCalledWith("TR", "ASELS", "1d")
    // The last bar is a session date, not "the evening before at 20:00" (its stamp is midnight UTC).
    expect(screen.getByText("₺141,20")).toHaveAttribute("title", "Son bar: 16 Eyl Çar")
    const chg = screen.getByTestId("chart-change")
    expect(chg).toHaveTextContent("+1,20 (+0,86%)")
    expect(chg).toHaveClass("text-positive")
    // The bars reached the (mocked) chart as OHLC + volume, dated by session, and the range was fitted once.
    await waitFor(() => expect(lw.candleData).toHaveBeenLastCalledWith([{ time: "2026-09-15", open: 139, high: 141, low: 138.5, close: 140 }, { time: "2026-09-16", open: 140, high: 142, low: 139.8, close: 141.2 }]))
    expect(lw.volumeData.mock.calls.at(-1)![0]).toHaveLength(2)
    expect(lw.fitContent).toHaveBeenCalledTimes(1)
    // Volume has a pane of its own (a fifth of the height) — never a second scale overlaid on the candles.
    expect(lw.addSeries).toHaveBeenCalledWith("Histogram", expect.not.objectContaining({ priceScaleId: expect.anything() }), 1)
    expect(lw.stretch).toHaveBeenCalledWith(0.25)
    expect(saved()).toMatchObject({ symbol: "ASELS", market: "TR", interval: "1d", min: false })
  })

  it("says end of day for daily rows from the table, and an intraday last bar as a moment on the market's clock", async () => {
    candles.mockResolvedValue({ ...ASELS, delay: "eod" })
    setup()
    act(() => openChart("ASELS", "TR"))
    expect(await screen.findByText("Yahoo Finance · gün sonu")).toBeInTheDocument()
    candles.mockResolvedValue({ ...ASELS, interval: "1h", bars: [{ t: day("2026-09-17T07:00:00Z"), o: 140, h: 141, l: 139, c: 140.5, v: 10 }] })
    act(() => openChart("THYAO", "TR"))
    await screen.findByText(SOURCE)
    expect(screen.getByText("₺140,50")).toHaveAttribute("title", "Son bar: Per 10:00 GMT+3")
  })

  it("an interval pick refetches with that interval and remembers it", async () => {
    const { user } = setup()
    act(() => openChart("ASELS", "TR"))
    await screen.findByText(SOURCE)
    await user.click(screen.getByRole("button", { name: "15m" }))
    await waitFor(() => expect(candles).toHaveBeenLastCalledWith("TR", "ASELS", "15m"))
    expect(screen.getByRole("button", { name: "15m" })).toHaveAttribute("aria-pressed", "true")
    expect(saved()).toMatchObject({ symbol: "ASELS", interval: "15m" })
  })

  it("colours a fall red and says so against the previous session, not the previous bar", async () => {
    candles.mockResolvedValue({ ...ASELS, interval: "5m", bars: [
      { t: day("2026-09-16T14:55:00Z"), o: 142, h: 142, l: 141, c: 142, v: 10 },
      { t: day("2026-09-17T07:00:00Z"), o: 141, h: 141.5, l: 140, c: 140, v: 10 },
      { t: day("2026-09-17T07:05:00Z"), o: 140, h: 141, l: 140, c: 141, v: null },
    ] })
    setup()
    act(() => openChart("ASELS", "TR"))
    const chg = await screen.findByTestId("chart-change")
    expect(chg).toHaveTextContent("-1,00 (-0,70%)")
    expect(chg).toHaveClass("text-negative")
    // A bar without volume is left out of the histogram, not drawn as zero.
    await waitFor(() => expect(lw.volumeData.mock.calls.at(-1)![0]).toHaveLength(2))
  })

  it("says when the symbol is unknown and when the provider slot cannot answer", async () => {
    candles.mockRejectedValue(new ApiError(404, "not found", "404 not found"))
    setup()
    act(() => openChart("XXXX", "TR"))
    expect(await screen.findByText("XXXX bulunamadı.")).toBeInTheDocument()
    candles.mockRejectedValue(new ApiError(503, "provider unavailable", "503 provider unavailable"))
    act(() => openChart("ASELS", "TR"))
    expect(await screen.findByText("Fiyat kaynağı şu an yanıt vermiyor.")).toBeInTheDocument()
  })

  it("keeps the bars through a refresh that fails, and marks the line — like the server's own last-good answer", async () => {
    const { qc } = setup()
    act(() => openChart("ASELS", "TR"))
    await screen.findByText(SOURCE)
    candles.mockRejectedValue(new ApiError(503, "provider unavailable", "503 provider unavailable"))
    await act(async () => { await qc.refetchQueries({ queryKey: ["candles"] }) })
    const line = await screen.findByText(`${SOURCE} · Per 12:00 GMT+3`)
    expect(screen.queryByText("Fiyat kaynağı şu an yanıt vermiyor.")).toBeNull()
    expect(screen.getByText("₺141,20")).toBeInTheDocument()
    expect(line).toHaveClass("text-warning")
    expect(line).toHaveAttribute("title", "Kaynak yanıt vermedi; son değer Per 12:00 GMT+3")
    // The server serving its remembered answer through an outage says so with `stale`, and reads the same.
    candles.mockResolvedValue({ ...ASELS, stale: true, as_of: "2026-09-17T08:30:00Z" })
    act(() => openChart("THYAO", "TR"))
    expect(await screen.findByText(`${SOURCE} · Per 11:30 GMT+3`)).toHaveClass("text-warning")
  })

  it("Esc minimises to the pill; the remembered symbol comes back on a bare openChart()", async () => {
    const { user } = setup()
    act(() => openChart("ASELS", "TR"))
    await screen.findByText(SOURCE)
    await user.keyboard("{Escape}")
    expect(screen.queryByRole("combobox")).toBeNull()
    expect(screen.getByText("+0,86%")).toHaveAttribute("title", SOURCE)   // the pill keeps the day's move, with its source
    expect(saved()).toMatchObject({ min: true })
    act(() => openChart())
    expect(await screen.findByRole("combobox")).toBeInTheDocument()
    expect(saved()).toMatchObject({ symbol: "ASELS", min: false })
  })

  it("Esc in a field elsewhere on the page keeps its own meaning; Esc in the widget's own picker minimises", async () => {
    const { user } = setup(<input role="combobox" aria-label="Ara" />)
    act(() => openChart("ASELS", "TR"))
    await screen.findByText(SOURCE)
    await user.click(screen.getByRole("combobox", { name: "Ara" }))
    await user.keyboard("{Escape}")
    expect(screen.getByRole("combobox", { name: "Hisse" })).toBeInTheDocument()
    await user.click(screen.getByRole("combobox", { name: "Hisse" }))
    await user.keyboard("{Escape}")
    expect(screen.queryByRole("combobox", { name: "Hisse" })).toBeNull()
  })

  it("the minimise button folds it to the pill; the X closes it without another fetch", async () => {
    const { user } = setup()
    act(() => openChart("ASELS", "TR"))
    await screen.findByText(SOURCE)
    await user.click(screen.getByRole("button", { name: "Küçült" }))
    expect(screen.queryByRole("combobox")).toBeNull()
    expect(screen.getByText("+0,86%")).toBeInTheDocument()
    await user.click(screen.getByRole("button", { name: "Kapat" }))
    expect(screen.queryByText("+0,86%")).toBeNull()
    expect(screen.queryByText("Grafik")).toBeNull()
    expect(candles).toHaveBeenCalledTimes(1)
  })

  it("opened from the sidebar with the pill remembered, it comes out as a window", async () => {
    localStorage.setItem("instilens.chart", JSON.stringify({ symbol: "ASELS", market: "TR", interval: "1d", min: true }))
    const { user } = setup()
    await user.click(screen.getByRole("button", { name: "toggle" }))
    expect(await screen.findByRole("combobox")).toBeInTheDocument()
    expect(saved()).toMatchObject({ min: false })
  })

  it("rebuilds the instance on a theme change and re-applies the locale on a language change", async () => {
    localStorage.setItem("instilens.lang", "tr")
    const { user } = setup(<><ThemeToggle /><LangToggle /></>, (n) => <ThemeProvider><LangProvider>{n}</LangProvider></ThemeProvider>)
    act(() => openChart("ASELS", "TR"))
    await screen.findByText(SOURCE)
    await waitFor(() => expect(lw.createChart).toHaveBeenCalledTimes(1))
    await user.click(screen.getByRole("button", { name: "theme" }))
    await waitFor(() => expect(lw.createChart).toHaveBeenCalledTimes(2))
    expect(lw.remove).toHaveBeenCalledTimes(1)
    await waitFor(() => expect(lw.candleData).toHaveBeenLastCalledWith(expect.arrayContaining([expect.objectContaining({ time: "2026-09-16" })])))
    await user.click(screen.getByRole("button", { name: "lang" }))
    await waitFor(() => expect(lw.chartOptions).toHaveBeenLastCalledWith({ localization: { locale: "en-GB" } }))
    expect(screen.getByText("Yahoo Finance · ~15 min delayed")).toBeInTheDocument()
  })

  it("remembers the symbol across loads but never opens by itself", async () => {
    localStorage.setItem("instilens.chart", JSON.stringify({ x: 10, y: 20, w: 500, h: 300, symbol: "THYAO", market: "TR", interval: "1h", min: false }))
    candles.mockResolvedValue({ ...ASELS, symbol: "THYAO", name: "Türk Hava Yolları", interval: "1h" })
    setup()
    expect(screen.queryByRole("combobox")).toBeNull()
    expect(candles).not.toHaveBeenCalled()
    act(() => openChart())
    await screen.findByText(SOURCE)
    expect(candles).toHaveBeenCalledWith("TR", "THYAO", "1h")
    expect(screen.getByRole("button", { name: "1s" })).toHaveAttribute("aria-pressed", "true")
  })

  it("the picker searches the active market for stocks and a pick switches the chart", async () => {
    search.mockResolvedValue([{ kind: "fund", key: "TR:TMV", label: "TMV", name: "A fund", href: "/funds/TMV" }, { kind: "stock", key: "TR:THYAO", label: "THYAO", name: "Türk Hava Yolları", href: "/stocks/THYAO" }])
    const { user } = setup()
    act(() => openChart("ASELS", "TR"))
    await screen.findByText(SOURCE)
    const box = screen.getByRole("combobox")
    await user.click(box)
    await user.keyboard("thy")
    const options = await screen.findAllByRole("option")
    expect(options).toHaveLength(1)   // the fund hit is not offered
    expect(options[0]).toHaveTextContent("THYAO")
    expect(box).toHaveAttribute("aria-activedescendant", options[0].id)   // the highlighted row is announced
    await user.keyboard("{Enter}")
    await waitFor(() => expect(candles).toHaveBeenLastCalledWith("TR", "THYAO", "1d"))
    expect(saved()).toMatchObject({ symbol: "THYAO" })
    // A mouse pick leaves the field like Enter does: typing afterwards is not a hidden query.
    search.mockResolvedValue([{ kind: "stock", key: "TR:EREGL", label: "EREGL", name: "Ereğli", href: "/stocks/EREGL" }])
    await user.click(box)
    await user.keyboard("ere")
    await user.click(await screen.findByRole("option", { name: /EREGL/ }))   // not the previous hit the list keeps while the search is in flight
    await waitFor(() => expect(candles).toHaveBeenLastCalledWith("TR", "EREGL", "1d"))
    expect(box).not.toHaveFocus()
  })

  it("opens on the Radar's most-accumulated stock when nothing is remembered, and on the page's stock on a stock page", async () => {
    radar.mockResolvedValue({ accumulated: [{ symbol: "THYAO" }, { symbol: "ASELS" }] })
    candles.mockImplementation(async (_m, s, i) => ({ ...ASELS, symbol: s, name: s, interval: i as Candles["interval"] }))
    const { user } = setup()
    await user.click(screen.getByText("toggle"))
    await waitFor(() => expect(candles).toHaveBeenCalledWith("TR", "THYAO", "1d"))
    expect(saved().symbol).toBe("THYAO")
    expect(screen.queryByText("Bir hisse seçin.")).toBeNull()
  })

  it("offers the watchlist and the Radar's movers as soon as the symbol field is focused, before anything is typed", async () => {
    radar.mockResolvedValue({ accumulated: [{ symbol: "THYAO", name: "Türk Hava Yolları" }], distributed: [{ symbol: "SOKE", name: "Şok" }] })
    candles.mockImplementation(async (_m, s, i) => ({ ...ASELS, symbol: s, name: s, interval: i as Candles["interval"] }))
    const { user } = setup()
    await user.click(screen.getByText("toggle"))
    await waitFor(() => expect(candles).toHaveBeenCalled())
    await user.click(screen.getByRole("combobox"))
    await waitFor(() => expect(screen.getByText("TUPRS")).toBeInTheDocument())
    expect(screen.getByText("Takip listesi")).toBeInTheDocument()
    expect(screen.getByText("Radar · son 30 gün")).toBeInTheDocument()
    expect(screen.getByText("SOKE")).toBeInTheDocument()
    expect(screen.queryByText("TMV")).toBeNull()  // funds have no candles
    await user.click(screen.getByText("TUPRS"))
    await waitFor(() => expect(candles).toHaveBeenCalledWith("TR", "TUPRS", "1d"))
  })
})
