"""Pydantic contracts between layers.

Raw payloads (what an adapter hands to the parser) and normalized objects (what the parser hands
to the engines) live here so both sides agree on one shape. Adapters for other sources (SEC 13F)
must produce the same normalized objects.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from instilens.domain.enums import Confidence, DisclosureKind, Market, Side, Source

# --------------------------------------------------------------------------- raw (adapter → parser)


class RawDisclosure(BaseModel):
    market: Market
    source: Source
    source_id: str
    kind: DisclosureKind
    published_at: datetime
    raw_uri: str | None = None
    payload: dict


class KapTransactionRow(BaseModel):
    """One row of the KAP 'Pay Alım Satım Bildirimi' transaction table."""

    transaction_date: date
    side: Literal["ALIS", "SATIS"]
    nominal: int = Field(gt=0)
    price: Decimal | None = None
    exchange: str = "BIST"


class KapShareTransactionPayload(BaseModel):
    """Canonical shape of a KAP share-transaction disclosure by a portfolio management company.

    The production adapter maps the official API response onto this; the fixture adapter reads it
    verbatim. Keeping one canonical payload means the parser never sees vendor-specific JSON.
    """

    member_oid: str
    member_name: str
    subject_symbol: str
    subject_name: str | None = None
    related_fund_codes: list[str] = Field(default_factory=list)
    rows: list[KapTransactionRow] = Field(min_length=1)
    ownership_before_pct: Decimal | None = None
    ownership_after_pct: Decimal | None = None
    amends_source_id: str | None = None  # set on "Düzeltme" disclosures


class KapHoldingRow(BaseModel):
    symbol: str
    quantity: int = Field(ge=0)
    market_value: Decimal | None = None
    weight_pct: Decimal | None = None


class KapPortfolioReportPayload(BaseModel):
    fund_code: str
    fund_name: str | None = None
    member_oid: str | None = None
    member_name: str | None = None
    as_of: date
    total_value: Decimal | None = None
    holdings: list[KapHoldingRow]


# --------------------------------------------------------------------------- normalized (parser → engines)


class NormalizedTransactionEvent(BaseModel):
    market: Market
    source: Source
    source_id: str
    instrument_symbol: str
    institution_ref: str
    institution_name: str
    fund_codes: list[str]
    side: Side
    buy_nominal: int
    sell_nominal: int
    net_nominal: int
    avg_price: Decimal | None
    net_value: Decimal | None
    effective_date: date
    published_at: datetime
    ownership_before_pct: Decimal | None
    ownership_after_pct: Decimal | None
    confidence: Confidence

    @property
    def fund_allocations(self) -> dict[str, int | None]:
        """Per-fund nominal. Only known when EXACT; otherwise None ("allocation unknown")."""
        if self.confidence is Confidence.EXACT and len(self.fund_codes) == 1:
            return {self.fund_codes[0]: self.net_nominal}
        return {code: None for code in self.fund_codes}

    @model_validator(mode="after")
    def _net_is_consistent(self) -> NormalizedTransactionEvent:
        if self.net_nominal != self.buy_nominal - self.sell_nominal:
            raise ValueError("net_nominal must equal buy_nominal - sell_nominal")
        return self


class NormalizedHolding(BaseModel):
    instrument_symbol: str
    quantity: int
    market_value: Decimal | None = None
    weight_pct: Decimal | None = None


class NormalizedSnapshot(BaseModel):
    market: Market
    source: Source
    source_id: str | None
    fund_code: str
    fund_name: str | None
    institution_ref: str | None
    institution_name: str | None
    as_of: date
    total_value: Decimal | None
    holdings: list[NormalizedHolding]
    confidence: Confidence = Confidence.EXACT
