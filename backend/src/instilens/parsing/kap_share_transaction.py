"""KAP 'Pay Alım Satım Bildirimi' → NormalizedTransactionEvent.

This is where the confidence law is enforced:
- exactly one related fund → EXACT, the amount is attributed to that fund;
- several (or zero) related funds → GROUPED, the amount stays at institution level and every fund
  gets `allocated_nominal = NULL`. We never split an aggregate evenly — we do not know the split.
"""

from decimal import Decimal

from instilens.domain.enums import Confidence, Side
from instilens.domain.schemas import (
    KapShareTransactionPayload,
    NormalizedTransactionEvent,
    RawDisclosure,
)


def parse_share_transaction(raw: RawDisclosure) -> NormalizedTransactionEvent:
    payload = KapShareTransactionPayload.model_validate(raw.payload)

    buy = sum(r.nominal for r in payload.rows if r.side == "ALIS")
    sell = sum(r.nominal for r in payload.rows if r.side == "SATIS")
    net = buy - sell

    if buy and sell:
        side = Side.MIXED
    elif buy:
        side = Side.BUY
    else:
        side = Side.SELL

    priced = [r for r in payload.rows if r.price is not None]
    avg_price: Decimal | None = None
    if priced:
        total_nominal = sum(r.nominal for r in priced)
        avg_price = sum(r.nominal * r.price for r in priced) / Decimal(total_nominal)  # type: ignore[operator]
    net_value = (Decimal(net) * avg_price) if avg_price is not None else None

    fund_codes = sorted({c.strip().upper() for c in payload.related_fund_codes if c.strip()})
    confidence = Confidence.EXACT if len(fund_codes) == 1 else Confidence.GROUPED

    return NormalizedTransactionEvent(
        market=raw.market,
        source=raw.source,
        source_id=raw.source_id,
        instrument_symbol=payload.subject_symbol.strip().upper(),
        institution_ref=payload.member_oid,
        institution_name=payload.member_name,
        fund_codes=fund_codes,
        side=side,
        buy_nominal=buy,
        sell_nominal=sell,
        net_nominal=net,
        avg_price=avg_price,
        net_value=net_value,
        effective_date=max(r.transaction_date for r in payload.rows),
        published_at=raw.published_at,
        ownership_before_pct=payload.ownership_before_pct,
        ownership_after_pct=payload.ownership_after_pct,
        confidence=confidence,
    )
