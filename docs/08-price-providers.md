# 08 · Price providers

Where every price on the screen comes from, and how a licensed vendor plugs in without touching the callers.

## The interface (`backend/src/instilens/ingestion/prices/provider.py`)

One `PriceProvider` protocol serves both price paths:

| Method | Used by | Contract |
|---|---|---|
| `quotes(tickers) -> dict[ticker, Tick]` | header strip (`services/quotes`, `services/feed`) | `tickers` are the **provider's own symbols** (`USDTRY=X`, `XU100.IS`, `^GSPC` for Yahoo). A ticker the provider cannot answer is absent, never invented. |
| `daily_bars(market, symbols, start) -> list[Bar]` | `ingestion/prices.load_prices` → `market_prices`; `services/candles` for a daily range the table cannot answer | `symbols` are **instrument symbols** (`ASELS`); the provider maps them to its convention and skips what it cannot map. `Bar.symbol` is the instrument symbol again. |
| `intraday_bars(market, symbol, interval, lookback) -> list[Candle]` | `services/candles` → `/stocks/{symbol}/candles` (the chart widget) | One **instrument symbol**, `interval` in `5m` / `15m` / `1h`, the latest `lookback` bars oldest first, regular session only. A symbol it cannot map — or that the provider says it does not carry — yields `[]`; an answer with no bars at all is an outage (`ProviderUnavailable`), the same verdict `daily_bars` gives an empty frame. |
| `status() -> ProviderStatus` | `/admin/providers`, the feed heartbeat, fallback logic | `name, configured, connected, delay ("realtime" / "delayed" / "eod"), last_tick_at, error, note` — honest values, no optimism. Each process has its own instance, so `/admin/providers` shows the status **as seen by the feed process** while the feed is alive (`status_from: "feed"`) and the answering API worker's own instance otherwise (`"api"`). |

`Bar(symbol, trade_date, open, high, low, close, volume)` — `Decimal` prices, `int` volume, holes are `None`.
`Candle(symbol, at, open, high, low, close, volume)` — an intraday bar: `at` is its open instant, tz-aware UTC; the four prices are always present (a bar with a hole in any of them is dropped by the adapter, never padded), `volume` may be `None`.
`Tick(key, price, change_pct, bar_date, at)` — `at` is the exchange timestamp when the vendor gives one, `None` when only the fetch instant is known (Yahoo).

`StreamingPriceProvider` adds `stream(symbols) -> AsyncIterator[Tick]` for vendors with a push channel (WebSocket/MQTT). Yahoo does not implement it; nothing consumes it yet.

Errors: `ProviderUnavailable` (outage, refused request, adapter missing) and its subclass `ProviderNotConfigured` (credentials absent). `load_prices` logs and stops on either — the pipeline around it never dies with the feed; `services/quotes` keeps the last good numbers flagged `stale`.

### Selection

`resolve_provider()` reads the runtime setting `price_provider` (Admin → Ayarlar → Veri, or `INSTILENS_PRICE_PROVIDER`). The choice is honoured only when that provider reports `configured=True`; otherwise Yahoo answers and the fallback is logged **once** per process. `build_provider(name)` is the registry; one instance per name is kept so `status()` can remember the last successful call.

The API runs two uvicorn workers and a PUT to `/admin/settings` applies the override on the worker that served it only, so `/quotes` and `/admin/providers` re-apply the stored overrides at the top of every request (one read of the small `app_settings` table); the feed re-applies every iteration. The `.env`-only keys (the Matriks credentials among them) still need `pm2 restart instilens-api instilens-scheduler instilens-feed --update-env`.

Every quote carries `source` (provider name) and `delayed` (`true` unless the provider's delay is `realtime`), and every `market_prices` row carries `source` (`yahoo` today, `csv` for the fixture loader, the vendor's name later).

### Candles (`services/candles`, `GET /api/v1/stocks/{symbol}/candles?market=&interval=5m|15m|1h|1d&lookback=10..1000`)

The chart widget's series. Daily bars come from `market_prices` when every row of the range carries open/high/low and was written by a provider (the nightly `load_prices`); a range with a close-only legacy row or a CSV row (the CSV loader, which also lays a `csv` close over a provider row) is answered by the active provider's `daily_bars` for the **whole** range instead — the chart never draws a candle padded from a close, nor labels CSV data with a provider's name. Intraday bars always come from `intraday_bars`. Every provider answer sits in a 60 s in-process cache per (market, symbol, interval) — the newest 256 keys per worker, the fetch itself under a per-key lock so a slow symbol never delays another — and the last good answer is remembered: an outage serves it under its own `as_of` with `stale: true` (logged, no invented bar); with nothing remembered the route answers `503 {"detail": "provider unavailable"}` — the Matriks slot, once selected, gives that on every call. The payload names its `source`, `delay` (`delayed` / `realtime` from the provider, `eod` for table rows — last night's close whoever printed them) and `delayed`, `tz` is the exchange's zone (`Europe/Istanbul` / `America/New_York`), and each bar is `{t, o, h, l, c, v}` with `t` in epoch seconds UTC — a daily bar at 00:00 UTC of its session date, an intraday bar at its open instant. Like `/quotes`, the route re-applies the stored runtime settings first, so a provider switch on the other uvicorn worker is honoured.

Yahoo: `fetch_intraday(ticker, interval, lookback)` (the third and last function that talks to Yahoo; tests monkeypatch it with a `Ticker.history()`-shaped frame — flat OHLCV columns over a tz-aware `DatetimeIndex`) asks `Ticker.history(start=…, interval=…, prepost=False, auto_adjust=False)` for a window sized from `lookback` (bars per 6.5 h session, 7/5 for weekends, a week for holidays) and capped two days inside Yahoo's limits (one for the midnight start, one for a server date a day behind the exchange's) — **60 days for 5m/15m, 730 days for 1h**; a request beyond them is refused by Yahoo outright, not truncated, so 1000 bars of 15m is the most the window can hold. It switches yfinance's exception hiding off (process-wide, like the fundamentals adapter), so a symbol Yahoo has nothing for (`YFTickerMissingError`) is `[]` — "no bars" on the widget — while a transport failure stays an outage.

## Providers

| Name | Delay | Licence | Status | Where |
|---|---|---|---|---|
| `yahoo` | ~15 min delayed | unofficial (yfinance) — development and beta only | works; `connected` = the last call was answered **with data** (yfinance swallows network errors and returns an empty frame, so a frame that yields nothing is reported as an outage: `connected=false`, `error` set, `ProviderUnavailable` raised) | `ingestion/prices/yahoo.py` |
| `matriks` | reported as `delayed` until the vendor confirms the entitlement (item 7 below) | licensed, contract pending | **slot** — reports `configured` from the three env keys, `connected=false`, refuses every data call; while configured (and therefore selected) the refusal is its `error`, so the admin card and the feed heartbeat name it | `ingestion/prices/matriks.py` |

The Matriks module deliberately contains no HTTP or socket code, no URL and no guessed message shape. It becomes an adapter only after the vendor documentation answers the questions below; nothing about the protocol is assumed in the meantime.

## What the Matriks adapter needs from the vendor documentation

1. **Authentication** — API key vs. token exchange; header name; token lifetime and refresh; whether REST and streaming share credentials.
2. **REST history endpoint** — path and parameters for daily OHLCV; date range limits per call; whether adjusted and unadjusted series are both available; pagination. The same for intraday bars (`intraday_bars`): which bar sizes exist, how far back each one reaches, and whether the timestamp is the bar's open or close.
3. **Symbol convention** — BIST equity suffix (`.E`? plain?), index codes (`XU100`), FX pairs (`USDTRY`), and how US symbols are spelled if the feed carries them at all.
4. **Streaming channel** — WebSocket or MQTT; connection URL pattern; subscribe/unsubscribe message; topic naming per symbol; heartbeat/keepalive; reconnect and replay semantics.
5. **Message fields** — the tick payload: last price, previous close (or the field the % change must be computed from), bid/ask, volume, exchange timestamp and its timezone/precision, session/state flags.
6. **Rate limits** — REST calls per minute/day, maximum concurrent streams, maximum symbols per subscription.
7. **Entitlement** — is the licence delayed or realtime, per market; does the feed state this in-band (so `status().delay` can be read rather than configured).
8. **KAP news feed** — whether a KAP disclosure stream is part of the package, its message format and how it maps to `disclosures` (this would supersede the public KAP adapter for headlines only, never for portfolio facts).

## The quote feed process (`instilens feed`, pm2 `instilens-feed`)

`services/feed.run_forever` polls the active provider every `quotes_interval_s` (runtime setting, 15–600 s) while BIST or NYSE is in a pre/open/post window — otherwise every 10 minutes, waking early for the next session start — and appends a `quotes` live event (`services/live`) only when a price, % change, stale flag or market state differs from the last one it published. An empty payload (provider down at feed start, nothing remembered yet in that process) is never published: the tabs keep the API's last-good numbers. Open tabs replace their `/quotes` query data from that event; nothing refetches. A tab applies a `quotes` event only when its `as_of` is newer than what it holds (a reconnect replays missed events), and a replay page carries only its newest `quotes` row. The feed also sweeps live events older than a day, once a day, so the table stays bounded while the scheduler is stopped.

Its heartbeat lives in `app_settings` under the reserved key `_feed_status` (`running, last_run_at, interval_s, published, provider, provider_status, error` — `provider_status` is `status().as_dict()` of the provider as the feed process saw it on that run). `/admin/providers` reports `feed.running=true` only when the heartbeat says so **and** `last_run_at` is younger than 3 × `interval_s`. Reserved keys (`_` prefix) are never listed or accepted by `/admin/settings`.

## Env checklist

`INSTILENS_PRICE_PROVIDER=yahoo|matriks` · `INSTILENS_QUOTES_INTERVAL_S=60` · `INSTILENS_MATRIKS_API_KEY=` · `INSTILENS_MATRIKS_BASE_URL=` · `INSTILENS_MATRIKS_WS_URL=` (all listed in `backend/.env.example`) — the three Matriks keys stay empty until the contract and the documentation are in hand; `price_provider` and `quotes_interval_s` are also editable from the admin UI. Setting the three keys selects the slot, which then refuses every call: the strip goes stale and `load_prices` stops, with the refusal named on the admin card — leave them empty until the adapter exists. After editing `.env`: `pm2 restart instilens-api instilens-scheduler instilens-feed --update-env` (all three processes read it).
