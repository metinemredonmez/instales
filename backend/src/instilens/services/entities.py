"""Entity resolution: source identifiers → our instruments / institutions / funds.

Unknown entities are created with `is_verified=False` instead of failing the pipeline, so a new
fund or a renamed member never blocks ingestion. A review queue for unverified rows is the
operational counterpart (docs/02-architecture.md#entity-review).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.domain.enums import InstitutionKind, Market
from instilens.domain.models import Fund, Institution, Instrument, MarketRow

MARKETS = {
    Market.TR: MarketRow(code="TR", name="Borsa İstanbul", currency="TRY", timezone="Europe/Istanbul"),
    Market.US: MarketRow(code="US", name="US Equities", currency="USD", timezone="America/New_York"),
}

UNRESOLVED_INSTITUTION_CODE = "UNRESOLVED"


class EntityResolver:
    def __init__(self, session: Session) -> None:
        self.session = session

    def ensure_markets(self) -> None:
        for market, row in MARKETS.items():
            if self.session.get(MarketRow, market.value) is None:
                self.session.add(row)
        self.session.flush()

    def instrument(self, market: Market, symbol: str, name: str | None = None) -> Instrument:
        market = Market(market)
        if self.session.get(MarketRow, market.value) is None:
            self.ensure_markets()
        symbol = symbol.strip().upper()
        if symbol.startswith("CUSIP:"):
            return self._instrument_by_cusip(market, symbol[6:], name)
        stmt = select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol)
        found = self.session.scalar(stmt)
        if found:
            return found
        created = Instrument(market_code=market, symbol=symbol, name=name or symbol, is_verified=False)
        self.session.add(created)
        self.session.flush()
        return created

    def _instrument_by_cusip(self, market: Market, cusip: str, name: str | None) -> Instrument:
        found = self.session.scalar(select(Instrument).where(Instrument.market_code == market, Instrument.cusip == cusip))
        if found:
            return found
        # Unknown CUSIP: keep it visible as its own symbol until the review queue maps it to a ticker.
        created = Instrument(market_code=market, symbol=cusip, name=name or cusip, cusip=cusip, is_verified=False)
        self.session.add(created)
        self.session.flush()
        return created

    def load_cusip_map(self, rows: list[dict]) -> int:
        """rows: [{cusip, symbol, name}] → verified US instruments; existing CUSIP-symbol rows get renamed."""
        self.ensure_markets()  # fresh databases have no market rows yet; instruments reference them
        n = 0
        for r in rows:
            cusip, symbol = r["cusip"].strip().upper(), r["symbol"].strip().upper()
            inst = self.session.scalar(select(Instrument).where(Instrument.market_code == Market.US, Instrument.cusip == cusip))
            if inst is None:
                inst = self.session.scalar(select(Instrument).where(Instrument.market_code == Market.US, Instrument.symbol == symbol))
            if inst is None:
                inst = Instrument(market_code=Market.US, symbol=symbol, name=r.get("name") or symbol, cusip=cusip, is_verified=True)
                self.session.add(inst)
            else:
                inst.symbol, inst.cusip, inst.is_verified = symbol, cusip, True
                if r.get("name"):
                    inst.name = r["name"]
            n += 1
        self.session.flush()
        return n

    def institution(self, market: Market, source_ref: str | None, name: str | None) -> Institution:
        if source_ref:
            found = self.session.scalar(
                select(Institution).where(
                    Institution.market_code == market, Institution.source_ref == source_ref
                )
            )
            if found:
                return found
        if name:
            found = self.session.scalar(
                select(Institution).where(Institution.market_code == market, Institution.name == name)
            )
            if found:
                if source_ref and not found.source_ref:
                    found.source_ref = source_ref
                return found
        code = _code_from(name) if name else UNRESOLVED_INSTITUTION_CODE
        found = self.session.scalar(
            select(Institution).where(Institution.market_code == market, Institution.code == code)
        )
        if found:
            return found
        created = Institution(
            market_code=market,
            code=code,
            name=name or "Unresolved institution",
            kind=InstitutionKind.PORTFOLIO_MANAGEMENT_CO if market is Market.TR else InstitutionKind.ASSET_MANAGER,
            source_ref=source_ref,
            is_verified=False,
        )
        self.session.add(created)
        self.session.flush()
        return created

    def fund(self, code: str, institution: Institution, name: str | None = None) -> Fund:
        code = code.strip().upper()
        found = self.session.scalar(select(Fund).where(Fund.code == code))
        if found:
            # A fund that was first seen under UNRESOLVED gets re-parented once we learn its PYŞ.
            if found.institution.code == UNRESOLVED_INSTITUTION_CODE and institution.code != UNRESOLVED_INSTITUTION_CODE:
                found.institution = institution
            return found
        created = Fund(code=code, name=name or code, institution=institution, is_verified=False)
        self.session.add(created)
        self.session.flush()
        return created


def _code_from(name: str) -> str:
    cleaned = "".join(ch for ch in name.upper() if ch.isalnum() or ch == " ")
    return "_".join(cleaned.split())[:32] or UNRESOLVED_INSTITUTION_CODE
