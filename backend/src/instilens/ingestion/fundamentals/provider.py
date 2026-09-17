"""Fundamentals provider interface: reported financial statements and a trailing-metrics snapshot per instrument.

Same shape as the price registry (ingestion/prices/provider.py): a provider owns its symbol convention, reports
itself through `status()` and raises `ProviderUnavailable` when it cannot answer at all. Only Yahoo exists today
(free, unofficial — dev and beta); a licensed data vendor plugs into the same contract. `resolve_provider` is the
single switch every caller goes through; an unknown `fundamentals_provider` setting falls back to Yahoo and says so once.

Everything a provider hands back is a *reported* figure: canonical keys the filing did not carry are None, never
estimated. The one figure that rests on estimates is `forward_pe` (price over the consensus EPS estimate) — it is
labelled as such wherever it is shown. Analyst recommendation / target-price fields are deliberately not part of this
contract (SPK rule).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Literal, Protocol

from instilens.config import settings
from instilens.ingestion.prices.provider import (  # noqa: F401 — re-exported: one status/error vocabulary for every provider
    ProviderStatus,
    ProviderUnavailable,
)

log = logging.getLogger("instilens.fundamentals")

PROVIDERS: tuple[str, ...] = ("yahoo",)
DEFAULT_PROVIDER = "yahoo"

StatementKind = Literal["income", "balance", "cashflow"]
PeriodKind = Literal["annual", "quarterly"]
STATEMENT_KINDS: tuple[StatementKind, ...] = ("income", "balance", "cashflow")
PERIOD_KINDS: tuple[PeriodKind, ...] = ("annual", "quarterly")

# Canonical line items per statement kind — the only keys a Statement.items may carry (docs/03-data-model.md).
CANONICAL_KEYS: dict[StatementKind, tuple[str, ...]] = {
    "income": ("revenue", "cost_of_revenue", "gross_profit", "operating_income", "ebitda", "pretax_income", "net_income", "eps_diluted", "interest_expense"),
    "balance": ("total_assets", "total_liabilities", "equity", "total_debt", "cash", "current_assets", "current_liabilities"),
    "cashflow": ("operating_cf", "capex", "free_cf", "dividends_paid", "share_repurchase"),
}


@dataclass(frozen=True)
class Statement:
    """One reported statement for one period. `items` maps every canonical key of `kind` to the reported value
    (absolute, in `currency`) or None. `currency_assumed` is True when the provider did not state the reporting
    currency and the market default (TRY / USD) was used instead."""

    kind: StatementKind
    period_end: date
    currency: str | None
    items: dict[str, float | None]
    currency_assumed: bool = False


@dataclass(frozen=True)
class MetricsSnapshot:
    """Trailing valuation / profitability metrics as the provider states them on `as_of` (the fetch date).
    Ratios that are fractions at the provider are already ×100 here (12.3 = 12.3 %). Two currencies: market_cap,
    enterprise_value, week52_high/low and eps_ttm are priced off the listing and are in `quote_currency`; revenue_ttm,
    ebitda_ttm and net_income_ttm come from the filings and are in `currency`, the reporting currency the statements
    use (THYAO reports in USD and trades in TRY; a US ADR the other way round). The `_assumed` flags are True when
    that currency was not stated and the market default was used. Every field but `as_of` is optional: what the
    provider does not state stays None."""

    as_of: date
    currency: str | None = None
    quote_currency: str | None = None
    currency_assumed: bool = False
    quote_currency_assumed: bool = False
    market_cap: float | None = None
    enterprise_value: float | None = None
    pe: float | None = None
    forward_pe: float | None = None
    price_to_book: float | None = None
    price_to_sales: float | None = None
    ev_to_ebitda: float | None = None
    profit_margin: float | None = None
    operating_margin: float | None = None
    return_on_assets: float | None = None
    return_on_equity: float | None = None
    revenue_ttm: float | None = None
    ebitda_ttm: float | None = None
    net_income_ttm: float | None = None
    eps_ttm: float | None = None
    dividend_yield: float | None = None
    payout_ratio: float | None = None
    beta: float | None = None
    week52_high: float | None = None
    week52_low: float | None = None
    shares_outstanding: float | None = None
    float_shares: float | None = None
    short_percent_of_float: float | None = None


# The contract's snapshot keys, in payload order (everything on MetricsSnapshot except as_of / currency bookkeeping).
SNAPSHOT_KEYS: tuple[str, ...] = (
    "market_cap", "enterprise_value", "pe", "forward_pe", "price_to_book", "price_to_sales", "ev_to_ebitda",
    "profit_margin", "operating_margin", "return_on_assets", "return_on_equity", "revenue_ttm", "ebitda_ttm",
    "net_income_ttm", "eps_ttm", "dividend_yield", "payout_ratio", "beta", "week52_high", "week52_low",
    "shares_outstanding", "float_shares", "short_percent_of_float",
)


class FundamentalsProvider(Protocol):
    name: str

    def statements(self, market: str, symbol: str, period: PeriodKind) -> list[Statement]:
        """Income / balance / cashflow statements of one instrument for `period`, newest first within each kind.
        Empty when the provider has nothing for the symbol (or cannot map it); ProviderUnavailable on an outage."""
        ...

    def snapshot(self, market: str, symbol: str) -> MetricsSnapshot | None:
        """Trailing metrics of one instrument; None when the provider has nothing for the symbol."""
        ...

    def status(self) -> ProviderStatus: ...


def build_provider(name: str) -> FundamentalsProvider:
    """A fresh adapter for `name`; unknown names are a configuration error, not a silent Yahoo."""
    if name == "yahoo":
        from instilens.ingestion.fundamentals.yahoo import YahooFundamentals

        return YahooFundamentals()
    raise ValueError(f"unknown fundamentals provider {name!r} (one of {', '.join(PROVIDERS)})")


# One instance per name so a provider can remember its last successful call (status.last_tick_at / error).
_instances: dict[str, FundamentalsProvider] = {}
_fallback_logged: set[str] = set()


def _instance(name: str) -> FundamentalsProvider:
    if name not in _instances:
        _instances[name] = build_provider(name)
    return _instances[name]


def resolve_provider() -> FundamentalsProvider:
    """The configured provider (`fundamentals_provider` setting) when it is configured, else Yahoo. The fallback
    is logged once per name, not on every call."""
    name = settings.fundamentals_provider or DEFAULT_PROVIDER
    try:
        provider = _instance(name)
    except ValueError as exc:
        if name not in _fallback_logged:
            log.warning("fundamentals_provider: %s; using %s", exc, DEFAULT_PROVIDER)
            _fallback_logged.add(name)
        return _instance(DEFAULT_PROVIDER)
    status = provider.status()
    if status.configured:
        return provider
    if name not in _fallback_logged:
        log.warning("fundamentals_provider=%s is not configured (%s); using %s", name, status.note or status.error or "no credentials", DEFAULT_PROVIDER)
        _fallback_logged.add(name)
    return _instance(DEFAULT_PROVIDER)


def reset() -> None:
    """Forget cached instances and the once-only fallback log (tests)."""
    _instances.clear()
    _fallback_logged.clear()
