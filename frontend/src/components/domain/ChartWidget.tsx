import { useQuery } from "@tanstack/react-query"
import { useEffect, useId, useRef, useState, type KeyboardEvent } from "react"
import { CandlestickChart, Minus, X } from "lucide-react"
import type { IChartApi, ISeriesApi, Time, UTCTimestamp } from "lightweight-charts"
import { ApiError, api, type CandleInterval, type Market } from "@/lib/api"
import { INTERVALS, candleSource, closeChange, cssToRgba, loadChart, onOpenChart, openChart, saveChart, sessionDate, themeColor, themeColorAlpha, zonedTime } from "@/lib/chart"
import { fmtNum, fmtPrice, locale } from "@/lib/format"
import { useI18n } from "@/lib/i18n"
import { useMatch } from "react-router-dom"
import { useMarket } from "@/lib/market"
import { fmtInZone, fmtSessionDate, providerLabel, quoteSourceLabel, useQuotes } from "@/lib/quotes"
import { useTheme } from "@/lib/theme"
import { cn } from "@/lib/utils"
import { FloatingWindow } from "@/components/layout/FloatingWindow"
import { useSearch } from "@/components/layout/useSearch"

type Inst = { chart: IChartApi; candles: ISeriesApi<"Candlestick">; volume: ISeriesApi<"Histogram">; up: string; down: string }

/**
 * Floating candlestick window on the same mechanics as the live TV (FloatingWindow): opened from the sidebar, the
 * stock page, any symbol row (`openChart`, lib/chart) or ⌘K; remembers where it sat, its size, the last symbol and
 * interval; starts closed on every load. Bars come from /stocks/{symbol}/candles — daily from market_prices, intraday
 * from the price provider — and the provider's own delay is written next to the price on every render (end-of-day
 * rows say so), never a blanket "live"; an answer the server or this client could not refresh keeps its bars and says
 * when they were fetched. Refetches every 60 s while the window is open and the market's own clock says a session is
 * running (the header /quotes payload's `markets`; unknown counts as running), and for ~20 min past the last print so
 * Yahoo's lag still delivers the close after the bell. Esc minimises to the pill. Volume sits in its own pane under the
 * candles — one series per panel, never two scales in one.
 */
export function ChartWidget({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { t, lang } = useI18n()
  const { theme } = useTheme()
  const { market: activeMarket } = useMarket()
  const [saved] = useState(loadChart)   // parsed once; the effect below writes it back on every change
  // A remembered box is clamped to this viewport: a window left at the far right of a wide screen must still be reachable on a narrow one.
  const [pos, setPos] = useState({ x: Math.max(0, Math.min(saved?.x ?? window.innerWidth - 580, window.innerWidth - 120)), y: Math.max(0, Math.min(saved?.y ?? window.innerHeight - 440, window.innerHeight - 40)) })
  const [size, setSize] = useState({ w: Math.max(320, Math.min(saved?.w ?? 540, window.innerWidth - 32)), h: Math.max(220, Math.min(saved?.h ?? 360, window.innerHeight - 32)) })
  const [sym, setSym] = useState<{ symbol: string | null; market: Market | null }>({ symbol: saved?.symbol ?? null, market: saved?.market ?? null })
  const [interval, pickInterval] = useState<CandleInterval>(saved?.interval ?? "1d")
  const [min, setMin] = useState(saved?.min ?? false)
  const { symbol, market } = sym
  const visible = open && !min

  useEffect(() => { saveChart({ x: pos.x, y: pos.y, w: size.w, h: size.h, symbol, market, interval, min }) }, [pos, size, symbol, market, interval, min])
  // Any row's openChart(): take the symbol (in that row's market, else the active one) and come out of the pill.
  const active = useRef(activeMarket)
  useEffect(() => { active.current = activeMarket }, [activeMarket])
  useEffect(() => onOpenChart((r) => { if (r.symbol) setSym({ symbol: r.symbol, market: r.market ?? active.current }); setMin(false) }), [])
  // (Re)opened from the sidebar or ⌘K with the pill remembered: it comes out as a window, never as a 260 px pill in a corner.
  useEffect(() => { if (open) setMin(false) }, [open])
  // Esc anywhere minimises — unless something in front already took it (the ⌘K dialog prevents the default when it closes on Esc; the picker stops
  // its own) or it was pressed in a field elsewhere on the page (the header search, a form), which keeps its Esc; only the widget's own picker minimises.
  useEffect(() => {
    if (!visible) return
    const onKey = (e: globalThis.KeyboardEvent) => {
      if (e.key !== "Escape" || e.defaultPrevented) return
      const el = e.target as HTMLElement | null
      if (el?.closest?.("[role=dialog], [role=combobox], [role=listbox], input, textarea, select, [contenteditable]") && !el.closest("[data-chart-window]")) return
      setMin(true)
    }
    window.addEventListener("keydown", onKey)
    return () => window.removeEventListener("keydown", onKey)
  }, [visible])

  // Opened with nothing remembered (first use, or a cleared browser): default to the stock the page is about, else the
  // most-accumulated stock of the active market's Radar — a chart window should never open on "pick a stock".
  const onStock = useMatch("/stocks/:symbol")
  const radar = useQuery({ queryKey: ["radar", activeMarket, 30], queryFn: () => api.radar(activeMarket, 15, 30), enabled: visible && !symbol && !onStock, staleTime: 60_000 })
  useEffect(() => {
    if (!visible || symbol) return
    const fromPage = onStock?.params.symbol?.toUpperCase()
    const fromRadar = radar.data?.accumulated[0]?.symbol
    const pick = fromPage ?? fromRadar
    if (pick) setSym({ symbol: pick, market: activeMarket })
  }, [visible, symbol, onStock, radar.data, activeMarket])

  const quotes = useQuotes()
  const state = market ? quotes.data?.markets[market]?.state : undefined
  const inSession = state === undefined || state === "open"
  const q = useQuery({
    queryKey: ["candles", market, symbol, interval],
    queryFn: () => api.candles(market!, symbol!, interval),
    enabled: visible && !!symbol && !!market,
    // 60 s while a session runs — and for ~20 min past the newest bar, so the ~15 min-late close of a session that just ended still arrives.
    refetchInterval: (query) => { const t = query.state.data?.bars.at(-1)?.t; return inSession || (t !== undefined && Date.now() - t * 1000 < 20 * 60_000) ? 60_000 : false },
    staleTime: 30_000,
    // A missing symbol or a provider that says so is an answer, not a hiccup: show it, do not retry it into a delay.
    retry: (n, e) => n < 2 && !(e instanceof ApiError && (e.status === 404 || e.status === 503)),
  })

  // The chart instance lives outside React state; `ready` bumps when one is (re)built so the data effect runs against it.
  const boxRef = useRef<HTMLDivElement>(null)
  const inst = useRef<Inst | null>(null)
  const fitted = useRef("")
  const [ready, setReady] = useState(0)
  useEffect(() => {
    if (!visible) return
    let gone = false
    // Built on the next frame: the theme class lands on <html> in the provider's own effect, which runs after this one, and the colours
    // are read from it. The library itself comes in on first use — the window starts closed, so most loads never pay for it.
    const id = requestAnimationFrame(() => {
      import("lightweight-charts").then((lw) => {
        const el = boxRef.current
        if (gone || !el) return
        // The theme's own tokens (oklch in index.css, parsed by cssToRgba below); plain fallbacks only where no stylesheet answers.
        const text = themeColor("--muted-foreground", "#808080"), grid = themeColor("--border", "#80808040"), up = themeColor("--positive", "#26a69a"), down = themeColor("--negative", "#ef5350")
        const chart = lw.createChart(el, {
          autoSize: true,
          layout: { background: { type: lw.ColorType.Solid, color: "transparent" }, textColor: text, fontFamily: "'Geist Variable', sans-serif", fontSize: 10, colorParsers: [cssToRgba], panes: { separatorColor: grid, separatorHoverColor: grid, enableResize: false } },
          grid: { vertLines: { color: grid, style: lw.LineStyle.Dotted }, horzLines: { color: grid, style: lw.LineStyle.Dotted } },
          rightPriceScale: { borderVisible: false },
          timeScale: { borderVisible: false, timeVisible: true, secondsVisible: false },
          crosshair: { mode: lw.CrosshairMode.Normal },
          localization: { locale: locale() },
        })
        const candles = chart.addSeries(lw.CandlestickSeries, { upColor: up, downColor: down, borderVisible: false, wickUpColor: up, wickDownColor: down, priceFormat: { type: "price", precision: 2, minMove: 0.01 } })
        // Volume in a second pane on its own scale, a fifth of the height, the time axis shared — never an overlay on the candles' scale.
        const volume = chart.addSeries(lw.HistogramSeries, { priceFormat: { type: "volume" }, lastValueVisible: false, priceLineVisible: false }, 1)
        chart.panes()[1]?.setStretchFactor(0.25)
        inst.current = { chart, candles, volume, up: themeColorAlpha("--positive", 0.4, up), down: themeColorAlpha("--negative", 0.4, down) }
        fitted.current = ""
        setReady((n) => n + 1)
      })
    })
    return () => { gone = true; cancelAnimationFrame(id); inst.current?.chart.remove(); inst.current = null }
  }, [visible, theme])
  // The axis and crosshair print dates in the UI language; the widget sits outside the per-language page remount, so a switch is applied to the instance.
  useEffect(() => { inst.current?.chart.applyOptions({ localization: { locale: locale() } }) }, [lang, ready])

  useEffect(() => {
    const c = inst.current
    if (!c) return
    const d = q.data
    if (!d) { c.candles.setData([]); c.volume.setData([]); return }
    const daily = d.interval === "1d"
    // Daily bars are dates (the session), intraday bars instants shifted to the market's wall clock — see lib/chart.
    const time = (t: number): Time => daily ? sessionDate(t, d.tz) : (zonedTime(t, d.tz) as UTCTimestamp)
    // The library refuses anything but strictly ascending times; a duplicate stamp from the source is dropped, not drawn twice.
    const bars = d.bars.map((b) => ({ ...b, time: time(b.t) })).filter((b, i, all) => i === 0 || b.time > all[i - 1].time)
    c.candles.setData(bars.map((b) => ({ time: b.time, open: b.o, high: b.h, low: b.l, close: b.c })))
    c.volume.setData(bars.filter((b) => b.v !== null).map((b) => ({ time: b.time, value: b.v!, color: b.c >= b.o ? c.up : c.down })))
    c.chart.timeScale().applyOptions({ timeVisible: !daily })
    // A new symbol or interval starts from the whole range; the 60 s refresh keeps whatever the reader zoomed to.
    const key = `${d.market}:${d.symbol}:${d.interval}`
    if (fitted.current !== key) { fitted.current = key; c.chart.timeScale().fitContent() }
  }, [q.data, ready])

  if (!open) return null
  const d = q.data
  const chg = d ? closeChange(d.bars, d.tz) : null
  const tone = chg?.abs == null ? "text-muted-foreground" : chg.abs > 0 ? "text-positive" : chg.abs < 0 ? "text-negative" : "text-muted-foreground"
  const err = q.error
  // An error replaces the chart only while there is nothing to show: a refresh that failed keeps the bars on screen (and marks them below).
  const message = !symbol ? t("chartw.empty")
    : d ? (d.bars.length === 0 ? t("chartw.noBars") : null)
    : err instanceof ApiError && err.status === 404 ? t("chartw.notFound", { s: symbol })
    : err instanceof ApiError && err.status === 503 ? t("chartw.unavailable")
    : err ? t("chartw.error")
    : q.isLoading ? "…" : null
  const last = d?.bars.at(-1)
  // A daily bar is a session date, not an instant (its stamp is midnight UTC); an intraday bar is a wall-clock moment in the market's zone.
  const lastBarAt = d && last ? (d.interval === "1d" ? fmtSessionDate(sessionDate(last.t, d.tz)) : fmtInZone(new Date(last.t * 1000).toISOString(), d.tz, lang)) : null
  // "Yahoo Finance · ~15 dk gecikmeli" as the strip prints it; daily rows from the table are last night's close and say so.
  const sourceLine = d ? (d.delay === "eod" ? `${providerLabel(d.source, t)} · ${t("fresh.delay.eod")}` : quoteSourceLabel(candleSource(d.source, d.delayed), t)) : null
  // The server's last good answer through an outage, or a refresh that failed here: the bars stay, the line says when they were fetched.
  const stale = !!d && (!!d.stale || !!err)
  const fetchedAt = d ? fmtInZone(d.as_of, d.tz, lang) : ""

  return (
    <FloatingWindow
      x={pos.x} y={pos.y} w={size.w} h={size.h} min={min} minW={320} minH={220} pillW={260} onMove={setPos} onResize={setSize} resizeTitle={t("tv.resize")}
      title={
        <>
          <CandlestickChart className="size-3.5 text-primary" />
          <span className="font-semibold">{t("chartw.title")}</span>
          {symbol && <span className="truncate text-muted-foreground">{symbol}{!min && d?.name && d.name !== d.symbol ? ` · ${d.name}` : ""}</span>}
          {/* The pill is 260px: the symbol and the day's move fit, the price and name wait for the window. */}
          {min && chg?.pct != null && <span className={cn("num whitespace-nowrap", tone)} title={sourceLine ?? undefined}>{fmtNum(chg.pct, 2, true)}%</span>}
          <span className="ml-auto" />
          {!min && (
            <span className="mr-1 inline-flex overflow-hidden rounded border border-border text-[10px]" role="group" aria-label={t("chartw.interval")}>
              {INTERVALS.map((iv) => (
                <button key={iv} type="button" aria-pressed={iv === interval} onClick={() => pickInterval(iv)} className={cn("px-1.5 py-0.5", iv === interval ? "bg-accent text-foreground" : "text-muted-foreground hover:text-foreground")}>{t(`chartw.iv.${iv}`)}</button>
              ))}
            </span>
          )}
          <button onClick={() => setMin(!min)} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label={t("chartw.minimise")} title={t("chartw.hint")}><Minus className="size-3.5" /></button>
          <button onClick={onClose} className="rounded p-1 text-muted-foreground hover:bg-accent hover:text-foreground" aria-label={t("common.close")}><X className="size-3.5" /></button>
        </>
      }
    >
      <div className="flex flex-col" style={{ height: size.h }} data-chart-window>
        {/* One line: which stock, its last close against the previous session, who printed it and how late. */}
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-b border-border/60 px-3 py-1.5 text-xs">
          <SymbolPicker value={symbol} onPick={(s, m) => setSym({ symbol: s, market: m })} />
          {d && chg && (
            <>
              <span className="num font-semibold" title={lastBarAt ? t("chartw.lastBar", { t: lastBarAt }) : undefined}>{fmtPrice(chg.close, d.currency)}</span>
              <span className={cn("num", tone)} title={t("chartw.vsPrev")} data-testid="chart-change">
                {chg.abs == null ? "—" : `${fmtNum(chg.abs, 2, true)} (${fmtNum(chg.pct, 2, true)}%)`}
              </span>
              <span className={stale ? "text-warning" : "text-muted-foreground"} title={stale ? t("quotes.stale", { at: fetchedAt }) : t("chartw.asOf", { t: fetchedAt })}>{sourceLine}{stale ? ` · ${fetchedAt}` : ""}</span>
            </>
          )}
        </div>
        <div className="relative min-h-0 flex-1">
          {/* The canvas stays mounted through an error or an empty answer (the instance is costly) but is hidden, so no stale axis shows behind the message. */}
          <div ref={boxRef} className={cn("absolute inset-0", message && "invisible")} />
          {message && <div className="absolute inset-0 grid place-items-center px-6 text-center text-xs text-muted-foreground">{message}</div>}
          {d && d.bars.length > 0 && q.isFetching && <span className="absolute right-2 top-1 size-1.5 animate-pulse rounded-full bg-muted-foreground/60" aria-hidden />}
        </div>
      </div>
    </FloatingWindow>
  )
}

/**
 * Stock typeahead on the header search (useSearch, the active market), stocks only. Shows the current symbol while
 * idle; focusing selects it so typing replaces it. Enter on a hit takes the hit; Enter on a bare code takes the code
 * as typed — the API says whether it exists (404 → the widget's "not found"). Escape closes the list first and only
 * a second Escape reaches the window (which minimises).
 */
function SymbolPicker({ value, onPick }: { value: string | null; onPick: (symbol: string, market: Market) => void }) {
  const { t } = useI18n()
  const { q, setQ, debounced, rows, fetching, reset, market } = useSearch()
  const [editing, setEditing] = useState(false)
  const [open, setOpen] = useState(false)
  const [active, setActive] = useState(0)
  const listId = useId()
  const inputRef = useRef<HTMLInputElement>(null)
  const stocks = rows.filter((h) => h.kind === "stock")
  useEffect(() => { setActive(0) }, [stocks.length, debounced])
  // A pick leaves the field (the mouse path too, whose mousedown kept focus): what is typed next never drives a query nobody can see.
  const pick = (symbol: string) => { onPick(symbol.toUpperCase(), market); reset(); setOpen(false); setEditing(false); inputRef.current?.blur() }
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((a) => Math.min(a + 1, stocks.length - 1)) }
    else if (e.key === "ArrowUp") { e.preventDefault(); setActive((a) => Math.max(a - 1, 0)) }
    else if (e.key === "Enter") { e.preventDefault(); const h = stocks[active]; if (h) pick(h.label); else if (/^[A-Z0-9.-]{1,12}$/i.test(q.trim())) pick(q.trim()); (e.target as HTMLInputElement).blur() }
    else if (e.key === "Escape") { if (open && stocks.length > 0) { e.stopPropagation(); setOpen(false) } else (e.target as HTMLInputElement).blur() }
  }
  const showList = editing && open && debounced.length > 0
  return (
    <span className="relative">
      <input
        ref={inputRef}
        value={editing ? q : value ?? ""}
        onChange={(e) => { setQ(e.target.value); setOpen(true) }}
        onFocus={(e) => { setEditing(true); setQ(value ?? ""); e.target.select() }}
        onBlur={() => { setEditing(false); setOpen(false); reset() }}
        onKeyDown={onKey}
        role="combobox"
        aria-expanded={showList}
        aria-controls={listId}
        aria-activedescendant={showList && stocks[active] ? `${listId}-${active}` : undefined}
        aria-autocomplete="list"
        aria-label={t("common.stock")}
        placeholder={t("chartw.symbolPh")}
        autoComplete="off"
        spellCheck={false}
        className="h-6 w-24 rounded border border-border bg-background px-1.5 font-mono text-[11px] font-semibold uppercase outline-none placeholder:font-sans placeholder:font-normal placeholder:normal-case focus:ring-2 focus:ring-ring/40"
      />
      {showList && (
        <ul id={listId} role="listbox" className="absolute left-0 top-full z-20 mt-1 max-h-56 w-64 overflow-auto rounded-md border border-border bg-popover p-1 text-xs shadow-lg">
          {stocks.length === 0 && <li className="px-2.5 py-1.5 text-muted-foreground">{fetching ? t("search.searching") : t("search.noResults")}</li>}
          {stocks.map((h, i) => (
            <li key={h.key} id={`${listId}-${i}`} role="option" aria-selected={i === active} onMouseEnter={() => setActive(i)} onMouseDown={(e) => { e.preventDefault(); pick(h.label) }} className={cn("flex cursor-pointer items-center gap-2 rounded-[5px] px-2.5 py-1.5", i === active ? "bg-accent text-foreground" : "text-foreground")}>
              <span className="font-medium">{h.label}</span>
              <span className="truncate text-muted-foreground">{h.name}</span>
            </li>
          ))}
        </ul>
      )}
    </span>
  )
}

/** The small candle icon next to a symbol on Radar / Moves / Watchlist / Portfolio rows: opens the widget on that stock. */
export function ChartRowButton({ symbol, market, className }: { symbol: string; market: Market; className?: string }) {
  const { t } = useI18n()
  const label = t("chartw.openFor", { s: symbol })
  return (
    <button type="button" onClick={() => openChart(symbol, market)} aria-label={label} title={label} className={cn("inline-flex rounded p-0.5 align-middle text-muted-foreground/60 hover:bg-accent hover:text-foreground", className)}>
      <CandlestickChart className="size-3.5" />
    </button>
  )
}
