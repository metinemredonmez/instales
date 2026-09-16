"""SEC 13F-HR → NormalizedSnapshot. In the US universe the *filer* is both institution and "fund":
one 13F = one portfolio, so the fund code is the CIK. Instruments are keyed by CUSIP; the resolver
maps CUSIP → ticker when it knows it and otherwise creates an unverified instrument named by CUSIP.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field

from instilens.domain.enums import Confidence
from instilens.domain.schemas import NormalizedHolding, NormalizedSnapshot, RawDisclosure


class Sec13FHolding(BaseModel):
    cusip: str = Field(min_length=6, max_length=9)
    issuer: str = ""
    quantity: int = Field(ge=0)
    value_usd: int = Field(ge=0)
    weight_pct: Decimal | None = None


class Sec13FPayload(BaseModel):
    cik: str
    filer_name: str
    period: date
    amendment: bool = False
    amendment_type: str | None = None  # cover page: RESTATEMENT replaces the original, NEW HOLDINGS only adds to it
    amends_source_id: str | None = None  # accession of the 13F-HR this 13F-HR/A replaces (set by the adapter or the pipeline)
    holdings: list[Sec13FHolding]


def parse_13f(raw: RawDisclosure) -> NormalizedSnapshot:
    p = Sec13FPayload.model_validate(raw.payload)
    return NormalizedSnapshot(
        market=raw.market, source=raw.source, source_id=raw.source_id,
        fund_code=f"CIK{int(p.cik)}", fund_name=p.filer_name,
        institution_ref=str(int(p.cik)), institution_name=p.filer_name,
        as_of=p.period, total_value=Decimal(sum(h.value_usd for h in p.holdings)),
        holdings=[NormalizedHolding(instrument_symbol=f"CUSIP:{h.cusip}", quantity=h.quantity, market_value=Decimal(h.value_usd), weight_pct=h.weight_pct) for h in p.holdings],
        confidence=Confidence.EXACT,
    )
