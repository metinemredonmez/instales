"""KAP fund 'Portföy Dağılım Raporu' → NormalizedSnapshot.

A snapshot is EXACT about *holdings at a date*; the *changes* derived from two snapshots are
INFERRED (see engine/positions.py). Non-equity lines (bonds, repo, cash) are out of scope for the
MVP and must be filtered by the adapter before reaching this parser.
"""

from instilens.domain.schemas import (
    KapPortfolioReportPayload,
    NormalizedHolding,
    NormalizedSnapshot,
    RawDisclosure,
)


def parse_portfolio_report(raw: RawDisclosure) -> NormalizedSnapshot:
    payload = KapPortfolioReportPayload.model_validate(raw.payload)
    merged: dict[str, NormalizedHolding] = {}
    for row in payload.holdings:
        symbol = row.symbol.strip().upper()
        if symbol in merged:  # same stock listed twice (e.g. two lines) → sum
            prev = merged[symbol]
            merged[symbol] = NormalizedHolding(
                instrument_symbol=symbol,
                quantity=prev.quantity + row.quantity,
                market_value=_add(prev.market_value, row.market_value),
                weight_pct=_add(prev.weight_pct, row.weight_pct),
            )
        else:
            merged[symbol] = NormalizedHolding(
                instrument_symbol=symbol,
                quantity=row.quantity,
                market_value=row.market_value,
                weight_pct=row.weight_pct,
            )
    return NormalizedSnapshot(
        market=raw.market,
        source=raw.source,
        source_id=raw.source_id,
        fund_code=payload.fund_code.strip().upper(),
        fund_name=payload.fund_name,
        institution_ref=payload.member_oid,
        institution_name=payload.member_name,
        as_of=payload.as_of,
        total_value=payload.total_value,
        holdings=sorted(merged.values(), key=lambda h: h.instrument_symbol),
    )


def _add(a, b):
    if a is None or b is None:
        return None
    return a + b
