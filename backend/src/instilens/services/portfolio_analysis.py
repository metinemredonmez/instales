"""On-demand portfolio diagnostics; no new facts, scores, recommendations or trades are stored.

Prices stay Decimal through the calculation. The historical basket holds TODAY's quantities
constant: it is price behaviour, not the owner's realised/time-weighted portfolio return.
See docs/04-confidence-and-scoring.md for coverage gates and formulas.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instilens.domain.enums import Confidence, ParseStatus
from instilens.domain.models import (
    Disclosure,
    Fund,
    Institution,
    Instrument,
    MarketPrice,
    Portfolio,
    PortfolioPosition,
    PortfolioSnapshot,
    SnapshotHolding,
)
from instilens.services.ownership import staleness_days

LOOKBACK_DAYS = 366
MIN_RETURNS = 21
MAX_PRICE_AGE_DAYS = 7
ANNUAL_SESSIONS = 252
MIN_WEEKDAY_COVERAGE = Decimal("0.8")
ZERO = Decimal(0)
ONE = Decimal(1)


def _number(value: Decimal | None, places: int = 2) -> float | None:
    """JSON boundary only; no rounded payload values feed further calculations."""
    return float(round(value, places)) if value is not None else None


def basket_metrics(values: list[Decimal]) -> dict:
    """Positive aligned daily basket values; sample volatility and peak-to-trough loss."""
    returns = [b / a - ONE for a, b in zip(values, values[1:], strict=False)]
    mean = sum(returns, ZERO) / len(returns)
    variance = sum(((r - mean) ** 2 for r in returns), ZERO) / (len(returns) - 1)
    peak, drawdown = values[0], ZERO
    for value in values:
        peak = max(peak, value)
        drawdown = max(drawdown, ONE - value / peak)
    return {
        "price_change_pct": _number((values[-1] / values[0] - ONE) * 100),
        "annualized_volatility_pct": _number((variance * ANNUAL_SESSIONS).sqrt() * 100),
        "max_drawdown_pct": _number(drawdown * 100),
    }


def analyze(session: Session, portfolio: Portfolio, on: date | None = None) -> dict:
    on = on or datetime.now(ZoneInfo("Europe/Istanbul")).date()
    positions = list(session.scalars(select(PortfolioPosition).where(
        PortfolioPosition.portfolio_id == portfolio.id,
    ).order_by(PortfolioPosition.instrument_id)))
    ids = [p.instrument_id for p in positions]
    instruments = {i.id: i for i in session.scalars(select(Instrument).where(
        Instrument.id.in_(ids),
    ))} if ids else {}
    bars: dict[int, list[MarketPrice]] = defaultdict(list)
    if ids:
        for bar in session.scalars(select(MarketPrice).where(
            MarketPrice.instrument_id.in_(ids),
            MarketPrice.trade_date >= on - timedelta(days=LOOKBACK_DAYS),
            MarketPrice.trade_date <= on,
        ).order_by(MarketPrice.trade_date)):
            bars[bar.instrument_id].append(bar)

    sources, allocations, invalid, stale = [], [], [], []
    histories = {}
    for position in positions:
        instrument = instruments[position.instrument_id]
        history = bars[position.instrument_id]
        latest = history[-1] if history else None
        valid = latest is not None and latest.close.is_finite() and latest.close > ZERO
        if any(not bar.close.is_finite() or bar.close <= ZERO for bar in history):
            invalid.append(instrument.symbol)
        if latest and (on - latest.trade_date).days > MAX_PRICE_AGE_DAYS:
            stale.append(instrument.symbol)
        sources.append({
            "position_id": position.id, "instrument_id": instrument.id,
            "symbol": instrument.symbol,
            "latest_date": latest.trade_date.isoformat() if latest else None,
            "providers": sorted({b.source for b in history}),
            "observations": len(history),
        })
        histories[instrument.id] = {b.trade_date: b.close for b in history}
        if valid:
            allocations.append((instrument.symbol, position.quantity * latest.close))

    total = sum((value for _, value in allocations), ZERO)
    weights = sorted(((symbol, value / total) for symbol, value in allocations),
                     key=lambda pair: (-pair[1], pair[0])) if total > ZERO else []
    priced_symbols = {symbol for symbol, _ in weights}
    missing = [s["symbol"] for s in sources if s["symbol"] not in priced_symbols]
    concentration = {
        "priced_positions": len(weights), "total_positions": len(positions),
        "missing_symbols": missing, "stale_symbols": stale,
        "largest_weight_pct": _number(weights[0][1] * 100) if weights else None,
        "top3_weight_pct": _number(sum((w for _, w in weights[:3]), ZERO) * 100) if weights else None,
        "hhi": _number(sum((w * w for _, w in weights), ZERO) * 10000) if weights else None,
        "allocations": [{"symbol": symbol, "weight_pct": _number(weight * 100)} for symbol, weight in weights],
    }
    risk = {
        "status": "empty", "start": None, "end": None, "observations": 0,
        "min_returns": MIN_RETURNS, "missing_dates": 0, "invalid_symbols": invalid,
        "price_change_pct": None, "annualized_volatility_pct": None, "max_drawdown_pct": None,
    }
    if positions:
        if missing:
            risk["status"] = "missing_prices"
        elif invalid:
            risk["status"] = "invalid_prices"
        else:
            # Start when every current holding has history. Never forward-fill missing sessions.
            start = max(min(histories[i]) for i in ids)
            dates = [set(day for day in histories[i] if day >= start) for i in ids]
            common = sorted(set.intersection(*dates))
            union = set.union(*dates)
            # A weekly series must not be annualised as daily merely because all symbols align.
            weekdays = sum((common[0] + timedelta(days=n)).weekday() < 5
                           for n in range((common[-1] - common[0]).days + 1)) if common else 0
            weekday_coverage = Decimal(sum(day.weekday() < 5 for day in common)) / weekdays if weekdays else ZERO
            risk.update(start=common[0].isoformat() if common else None,
                        end=common[-1].isoformat() if common else None,
                        observations=len(common), missing_dates=len(union) - len(common))
            if stale:
                risk["status"] = "stale_prices"
            elif risk["missing_dates"] or weekday_coverage < MIN_WEEKDAY_COVERAGE or any((b - a).days > MAX_PRICE_AGE_DAYS for a, b in zip(common, common[1:], strict=False)):
                risk["status"] = "incomplete_history"
            elif len(common) < MIN_RETURNS + 1:
                risk["status"] = "insufficient_history"
            else:
                values = [sum((p.quantity * histories[p.instrument_id][day] for p in positions), ZERO) for day in common]
                risk.update(status="ready", **basket_metrics(values))
    return {
        "as_of": on.isoformat(), "lookback_days": LOOKBACK_DAYS,
        "concentration": concentration, "risk": risk, "price_sources": sources,
        "common_funds": common_funds(session, portfolio.market_code, ids, on),
    }


def common_funds(session: Session, market: str, ids: list[int], on: date) -> dict:
    """Funds whose newest eligible snapshot holds >=2 current stocks. No event allocation.

    Rank full books BEFORE matching stocks, so a fund's old holdings never reappear after exit.
    Every returned fund row retains its snapshot, disclosure and confidence.
    """
    if len(ids) < 2:
        return {"total": 0, "rows": []}
    latest = select(
        PortfolioSnapshot.id.label("id"),
        func.row_number().over(partition_by=PortfolioSnapshot.fund_id,
                               order_by=PortfolioSnapshot.as_of.desc()).label("rank"),
    ).join(Fund, Fund.id == PortfolioSnapshot.fund_id).join(Institution).join(
        Disclosure, Disclosure.id == PortfolioSnapshot.disclosure_id,
    ).where(
        Institution.market_code == market, Disclosure.market_code == market,
        PortfolioSnapshot.as_of <= on,
        Disclosure.published_at < datetime.combine(on + timedelta(days=1), time.min),
        Disclosure.is_superseded.is_(False), Disclosure.parse_status == ParseStatus.PARSED,
    ).subquery()
    records = session.execute(select(PortfolioSnapshot, Fund, Instrument.symbol).join(
        latest, (latest.c.id == PortfolioSnapshot.id) & (latest.c.rank == 1),
    ).join(Fund, Fund.id == PortfolioSnapshot.fund_id).join(
        SnapshotHolding, SnapshotHolding.snapshot_id == PortfolioSnapshot.id,
    ).join(Instrument, Instrument.id == SnapshotHolding.instrument_id).where(
        PortfolioSnapshot.as_of >= on - timedelta(days=staleness_days(market)),
        PortfolioSnapshot.confidence != Confidence.GROUPED,
        SnapshotHolding.instrument_id.in_(ids), SnapshotHolding.quantity > 0,
        Instrument.market_code == market,
    ))
    funds = {}
    for snapshot, fund, symbol in records:
        row = funds.setdefault(fund.code, {
            "code": fund.code, "name": fund.name, "as_of": snapshot.as_of.isoformat(),
            "snapshot_id": snapshot.id, "disclosure_id": snapshot.disclosure_id,
            "confidence": snapshot.confidence, "symbols": [],
        })
        row["symbols"].append(symbol)
    rows = [row for row in funds.values() if len(row["symbols"]) >= 2]
    for row in rows:
        row["symbols"].sort()
    rows.sort(key=lambda row: (-len(row["symbols"]), row["code"]))
    return {"total": len(rows), "rows": rows[:10]}
