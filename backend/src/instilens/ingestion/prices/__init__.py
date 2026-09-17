"""Daily price loader: `market_prices` rows from whichever provider `resolve_provider()` picks.

Provider-agnostic on purpose — the CLI, scheduler and admin pipeline call this and never a vendor module.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.domain.models import Instrument, MarketPrice
from instilens.ingestion.prices.provider import Bar, ProviderUnavailable, resolve_provider

log = logging.getLogger("instilens.prices")


def load_prices(session: Session, market: str, days: int = 400, symbols: list[str] | None = None, batch: int = 50) -> int:
    """Upsert OHLCV bars since `days` ago for every instrument of `market` (or the given `symbols`); each row
    records the provider that printed it in `source`. Returns the number of bars written. A provider outage is
    logged and ends the run early (what was written stays) — the pipeline around it must not die with the feed."""
    provider = resolve_provider()
    q = select(Instrument).where(Instrument.market_code == market)
    if symbols:
        q = q.where(Instrument.symbol.in_([s.upper() for s in symbols]))
    instruments = {i.symbol: i for i in session.scalars(q)}
    names = list(instruments)
    start = date.today() - timedelta(days=days)
    written = 0
    for i in range(0, len(names), batch):
        by_symbol: dict[str, list[Bar]] = defaultdict(list)
        try:
            bars = provider.daily_bars(market, names[i : i + batch], start)
        except ProviderUnavailable as exc:
            log.warning("%s prices: %s unavailable, stopping after %s bars: %s", market, provider.name, written, exc)
            break
        for bar in bars:
            by_symbol[bar.symbol].append(bar)
        for symbol, symbol_bars in by_symbol.items():
            inst = instruments.get(symbol)
            if inst is None:
                log.warning("%s: %s answered for %s, which was not asked for — skipped", market, provider.name, symbol)
                continue
            existing = {
                d: row for d, row in session.execute(select(MarketPrice.trade_date, MarketPrice).where(MarketPrice.instrument_id == inst.id, MarketPrice.trade_date >= start))
            }
            for bar in symbol_bars:
                row = existing.get(bar.trade_date)
                if row is None:
                    row = MarketPrice(instrument_id=inst.id, trade_date=bar.trade_date, close=bar.close)
                    session.add(row)
                    existing[bar.trade_date] = row
                row.close, row.source = bar.close, provider.name
                # Yahoo leaves intermittent NaN holes in thin names and the scheduler re-runs the window nightly:
                # a hole never erases a value an earlier run printed.
                for field in ("open", "high", "low", "volume"):
                    value = getattr(bar, field)
                    if value is not None:
                        setattr(row, field, value)
                written += 1
        session.flush()
    return written
