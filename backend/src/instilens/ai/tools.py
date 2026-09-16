"""Provider-agnostic tool functions. Each returns a compact JSON string built from our own tables.

Bound to a SQLAlchemy session via `build_tools(session)`; the Claude engine wraps them with
`@beta_tool`, a local engine would expose the same callables through its own tool protocol.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from sqlalchemy.orm import Session

from instilens.services import analytics

SignalName = Literal[
    "ACCUMULATION",
    "DISTRIBUTION",
    "POSITIVE_DIVERGENCE",
    "NEGATIVE_DIVERGENCE",
    "NEW_POSITION_CLUSTER",
    "EXIT_CLUSTER",
]


def _dump(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def build_tools(session: Session, market: str = "TR") -> list[Callable[..., str]]:
    def get_radar(limit: int = 10) -> str:
        """Smart Money Radar: the most accumulated and most distributed stocks over the latest
        30-day window, plus the strongest active signals. Start here for "what are funds doing".

        Args:
            limit: Max rows per list (default 10).
        """
        return _dump(analytics.radar(session, market, limit))

    def get_stock(symbol: str) -> str:
        """Stock intelligence: Smart Money & Consensus scores with their breakdown ("why"),
        top buying and selling funds in the latest period, active signals, and recent
        disclosed transactions with confidence levels and source ids.

        Args:
            symbol: Ticker symbol, e.g. ASELS.
        """
        data = analytics.stock_detail(session, market, symbol)
        return _dump(data if data is not None else {"error": f"unknown symbol {symbol}"})

    def get_fund(code: str) -> str:
        """Fund intelligence: latest portfolio holdings, and NEW / ADD / REDUCE / EXIT activity in
        the latest reporting period, plus recent transaction disclosures involving the fund.

        Args:
            code: Fund code, e.g. TMV.
        """
        data = analytics.fund_detail(session, code)
        return _dump(data if data is not None else {"error": f"unknown fund {code}"})

    def list_events(limit: int = 20) -> str:
        """Live event feed: most recent disclosed buy/sell transactions by institutions, newest
        first, each with related funds, nominal amount, confidence and KAP source id.

        Args:
            limit: Max events (default 20).
        """
        return _dump(analytics.events(session, market, limit))

    def screen_stocks(
        min_smart_money_score: float | None = None,
        min_consensus_score: float | None = None,
        min_funds_increasing: int | None = None,
        min_funds_new: int | None = None,
        min_net_flow_value: float | None = None,
        max_price_change_30d_pct: float | None = None,
        signal_types: list[SignalName] | None = None,
        limit: int = 25,
    ) -> str:
        """Smart Money Screener. All filters are AND-ed; omit a filter to ignore it. Use it for
        questions like "stocks funds are accumulating while the price is falling"
        (min_funds_increasing + max_price_change_30d_pct < 0, or signal_types=["POSITIVE_DIVERGENCE"]).

        Args:
            min_smart_money_score: 0-100.
            min_consensus_score: 0-100.
            min_funds_increasing: Minimum number of funds/institutions that increased.
            min_funds_new: Minimum number of funds that opened a new position.
            min_net_flow_value: Minimum net institutional flow in market currency.
            max_price_change_30d_pct: Upper bound on 30-day price change, e.g. 0 for "not up".
            signal_types: Keep stocks with at least one of these active signals.
            limit: Max rows.
        """
        return _dump(
            analytics.screener(
                session,
                market,
                min_smart_money_score=min_smart_money_score,
                min_consensus_score=min_consensus_score,
                min_funds_increasing=min_funds_increasing,
                min_funds_new=min_funds_new,
                min_net_flow_value=min_net_flow_value,
                max_price_change_pct=max_price_change_30d_pct,
                signal_types=list(signal_types) if signal_types else None,
                limit=limit,
            )
        )

    return [get_radar, get_stock, get_fund, list_events, screen_stocks]
