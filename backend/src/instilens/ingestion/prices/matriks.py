"""Matriks price provider — a slot, not an adapter yet.

The vendor documentation (endpoints, symbol convention, message format, entitlements) has not arrived, so this
module deliberately contains no HTTP or socket code, no URLs and no guessed payloads. It reads the three
config keys, reports its status honestly and refuses every data call. What the real adapter needs from the
documentation is listed in docs/08-price-providers.md.
"""

from __future__ import annotations

from datetime import date

from instilens.config import settings
from instilens.ingestion.prices.provider import (
    Bar,
    Candle,
    ProviderNotConfigured,
    ProviderStatus,
    ProviderUnavailable,
    Tick,
)

NOTE = "Adapter awaits Matriks API documentation (endpoints, symbols, message format); see docs/08-price-providers.md"
REFUSAL = "matriks: no adapter yet — the keys are set but no request can be made; see docs/08-price-providers.md"


class MatriksProvider:
    name = "matriks"

    def _configured(self) -> bool:
        return bool(settings.matriks_api_key and settings.matriks_base_url and settings.matriks_ws_url)

    def _refuse(self) -> ProviderUnavailable:
        return ProviderUnavailable(REFUSAL) if self._configured() else ProviderNotConfigured(f"matriks: set INSTILENS_MATRIKS_API_KEY, _BASE_URL and _WS_URL. {NOTE}")

    def quotes(self, tickers: list[str]) -> dict[str, Tick]:
        raise self._refuse()

    def daily_bars(self, market: str, symbols: list[str], start: date) -> list[Bar]:
        raise self._refuse()

    def intraday_bars(self, market: str, symbol: str, interval: str, lookback: int) -> list[Candle]:
        raise self._refuse()

    def status(self) -> ProviderStatus:
        # configured = the keys are present; connected stays False because nothing is ever dialled. A configured slot
        # is the one resolve_provider selects, and it then refuses every call — that refusal is its `error`, so the
        # admin card and the feed heartbeat name it rather than showing a silent "not connected". The entitlement
        # (realtime or delayed) is unconfirmed until the vendor documentation says; "delayed" is the claim that
        # cannot overstate it.
        configured = self._configured()
        return ProviderStatus(self.name, configured=configured, connected=False, delay="delayed", last_tick_at=None, error=REFUSAL if configured else None, note=NOTE)
