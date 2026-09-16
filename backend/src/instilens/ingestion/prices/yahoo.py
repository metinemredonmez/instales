"""Prototype price feed via Yahoo Finance (yfinance). Delayed, unofficial, free — fine for
development and the beta; production uses a licensed vendor behind the same `load_prices` call.

Symbols: BIST tickers become `ASELS.IS`; US tickers are used as-is. Unknown/illiquid symbols
(or unmapped CUSIP placeholders) are skipped, never guessed.
"""

from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.domain.models import Instrument, MarketPrice

log = logging.getLogger(__name__)


def yahoo_symbol(market: str, symbol: str) -> str | None:
    if not symbol.isalpha() and market == "US":
        return None  # CUSIP placeholder
    return f"{symbol}.IS" if market == "TR" else symbol


def load_prices(session: Session, market: str, days: int = 400, symbols: list[str] | None = None, batch: int = 50) -> int:
    import yfinance as yf

    q = select(Instrument).where(Instrument.market_code == market)
    if symbols:
        q = q.where(Instrument.symbol.in_([s.upper() for s in symbols]))
    instruments = [i for i in session.scalars(q) if yahoo_symbol(market, i.symbol)]
    start = date.today() - timedelta(days=days)
    written = 0
    for i in range(0, len(instruments), batch):
        chunk = instruments[i : i + batch]
        tickers = [yahoo_symbol(market, x.symbol) for x in chunk]
        data = yf.download(tickers, start=start.isoformat(), auto_adjust=False, progress=False, group_by="ticker", threads=True)
        if data is None or data.empty:
            continue
        for inst, tk in zip(chunk, tickers, strict=True):
            try:
                frame = data[tk] if len(tickers) > 1 else data
                closes = frame["Close"].dropna()
            except (KeyError, TypeError):
                continue
            existing = {
                d: row for d, row in session.execute(select(MarketPrice.trade_date, MarketPrice).where(MarketPrice.instrument_id == inst.id, MarketPrice.trade_date >= start))
            }
            for ts, close in closes.items():
                d = ts.date()
                price = Decimal(str(round(float(close), 4)))
                if d in existing:
                    existing[d].close = price
                else:
                    session.add(MarketPrice(instrument_id=inst.id, trade_date=d, close=price))
                written += 1
        session.flush()
    return written
