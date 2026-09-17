"""Issuer CIK resolution: the SEC's company_tickers.json (official, free, refreshed daily) → `Instrument.sec_cik`.

EDGAR keys everything about an issuer — its submissions listing, its Form 4s, its 8-K/10-K/10-Q — by CIK, while our
US instruments are keyed by ticker (from the OpenFIGI CUSIP map). This is the bridge. Class shares differ in spelling
between the two worlds ("BRK/B" and "BRK.B" at data vendors, "BRK-B" at the SEC), so tickers are compared with every
separator folded to "-".
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.domain.enums import Market
from instilens.domain.models import Instrument
from instilens.ingestion.sec.edgar_client import EdgarClient

log = logging.getLogger("instilens.insiders")


def normalize_ticker(symbol: str) -> str:
    """One spelling for class-share tickers: BRK/B, BRK.B and BRK-B all become BRK-B."""
    return symbol.strip().upper().replace("/", "-").replace(".", "-")


def parse_tickers(data: dict) -> dict[str, str]:
    """company_tickers.json ({"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}) → normalized
    ticker → CIK as a string without leading zeros. A ticker listed twice keeps its first (largest) issuer."""
    out: dict[str, str] = {}
    for row in data.values():
        ticker, cik = row.get("ticker"), row.get("cik_str")
        if not ticker or cik in (None, ""):
            continue
        out.setdefault(normalize_ticker(str(ticker)), str(int(cik)))
    return out


def sync_ciks(session: Session, tickers: dict[str, str], *, only_missing: bool = True) -> int:
    """Store the CIK of every US instrument whose ticker the SEC map knows. CUSIP placeholders (an instrument named
    by its CUSIP because no ticker is mapped yet) have no ticker to look up and are skipped. With `only_missing`
    an instrument that already carries a CIK is left alone, so a hand-corrected value survives the daily sync.
    Returns the number of instruments updated."""
    n = 0
    stmt = select(Instrument).where(Instrument.market_code == Market.US)
    if only_missing:
        stmt = stmt.where(Instrument.sec_cik.is_(None))
    for inst in session.scalars(stmt):
        if inst.cusip and inst.symbol == inst.cusip:
            continue
        cik = tickers.get(normalize_ticker(inst.symbol))
        if cik and cik != inst.sec_cik:
            inst.sec_cik = cik
            n += 1
    session.flush()
    return n


def refresh_ciks(session: Session, client: EdgarClient, *, only_missing: bool = True) -> int:
    """`sync_ciks` with the live map (one request)."""
    tickers = parse_tickers(client.company_tickers())
    n = sync_ciks(session, tickers, only_missing=only_missing)
    log.info("sec ciks: %s instruments updated from %s tickers", n, len(tickers))
    return n
