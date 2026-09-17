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


class KapInsiderRow(BaseModel):
    """One day of a KAP insider filing's transaction table: nominal (TL — one share on BIST) bought or sold at the
    stated price. `price` is the single price or the stated average; a filing that only gives a range keeps it in
    `price_low` / `price_high` and leaves `price` None (never a midpoint we made up)."""

    transaction_date: date
    side: Literal["ALIS", "SATIS"]
    nominal: int = Field(gt=0)
    price: Decimal | None = None
    price_low: Decimal | None = None
    price_high: Decimal | None = None
    post_pct_stake: Decimal | None = None  # capital share after the day's transactions, as the filing states it


class KapInsiderPayload(BaseModel):
    """Canonical shape of a KAP 'Pay Alım Satım Bildirimi' filed for a person or a shareholder rather than a PYŞ:
    the filing an issuer publishes about its own director / executive / shareholder, or the one MKK relays on a
    person's behalf under "Kamuyu Aydınlatma Platformu" with the SPK form as a PDF. `party_name` is the acting
    party ("Ad Soyad / Ticaret Ünvanı" of the form, the "… tarafından" of the prose), `role_text` the "Görevi" /
    relationship text as filed, `signatory` the natural person who signed for a legal entity (their `signatory_role`
    is a job at that entity, not a relationship to the issuer). `party_is_issuer` marks the issuer trading its own
    shares (a buyback or a treasury-share sale)."""

    member_oid: str
    member_name: str  # who published: the issuer, or "KAMUYU AYDINLATMA PLATFORMU" for a relayed filing
    subject_symbol: str
    subject_name: str | None = None
    party_name: str
    party_kind: Literal["person", "company", "fund", "other"]
    party_is_issuer: bool = False
    role_text: str | None = None
    signatory: str | None = None
    signatory_role: str | None = None
    acting_with: str | None = None  # "Varsa Birlikte Hareket Eden Diğer Gerçek-Tüzel Kişiler"
    rows: list[KapInsiderRow] = Field(min_length=1)
    post_pct_stake: Decimal | None = None  # after the last row
    is_correction: bool = False
    amends_source_id: str | None = None  # the corrected disclosure's index when the page names it
    numbers_from: Literal["table", "form", "prose"] = "table"
    attachment_count: int = 0


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


class NormalizedInsiderRow(BaseModel):
    """One insider_transactions row to be: KAP ALIŞ → code P, SATIŞ → code S (no other codes exist on KAP)."""

    transaction_date: date
    code: Literal["P", "S"]
    nominal: int = Field(gt=0)
    price: Decimal | None
    price_low: Decimal | None
    price_high: Decimal | None
    post_pct_stake: Decimal | None


class NormalizedInsiderFiling(BaseModel):
    """A KAP person / shareholder filing as services/insiders stores it: the party with a stable key and the roles
    mapped from the Turkish title, the subject instrument, one row per day and side. `is_issuer` marks the company
    trading its own shares (listed, never counted as an insider purchase)."""

    market: Market
    source: Source
    source_id: str
    subject_symbol: str
    subject_name: str | None
    party_name: str
    party_key: str
    party_kind: Literal["person", "company", "fund", "other"]
    roles: str  # comma-joined: director, officer, shareholder, other — or "issuer"
    title: str | None  # the role text as filed, for a person
    is_issuer: bool
    rows: list[NormalizedInsiderRow] = Field(min_length=1)
    post_pct_stake: Decimal | None
    published_at: datetime
    is_correction: bool
    amends_source_id: str | None
    confidence: Confidence = Confidence.EXACT


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
