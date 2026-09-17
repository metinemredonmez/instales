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

    def get_news(symbol: str | None = None, limit: int = 15) -> str:
        """Recent headlines (title, source, time, link) with rule tags and AI tags (symbols, sentiment,
        relevance, Turkish summary). Pass a symbol to get only headlines about that stock.

        Args:
            symbol: Optional ticker, e.g. ASELS.
            limit: Max headlines (default 15).
        """
        return _dump(analytics.news(session, market, symbol, limit))

    def _moves(kind: str, window_days: int | None, limit: int, code: str | None = None) -> str:
        try:
            data = analytics.moves(session, market, kind=kind, window_days=window_days, fund_code=code, limit=limit)
        except ValueError as exc:
            return _dump({"error": str(exc)})
        return _dump(data if data is not None else {"error": f"unknown fund {code}"})

    def get_top_buys(window_days: int | None = None, limit: int = 10) -> str:
        """Stocks with the largest positive net institutional flow in the window, largest first. The payload
        carries as_of, window_days, window_start, total (rows before the limit) and rows. Each row:
        net_flow_value (market currency), net_qty, how many funds increased / reduced / opened / closed,
        party_count, and the top 5 parties behind it (fund or institution, activity NEW/ADD/REDUCE/EXIT,
        delta_qty, delta_value, weight change, period_end, confidence). These are observed, descriptive
        flows from portfolio reports and disclosed transactions — not recommendations. Use it for
        "who bought the most / what did funds buy most".

        Args:
            window_days: Lookback in days; omit for the market's own window (TR 30, US 100).
            limit: Max rows (default 10).
        """
        return _moves("buys", window_days, limit)

    def get_top_sells(window_days: int | None = None, limit: int = 10) -> str:
        """Stocks with the most negative net institutional flow in the window, most sold first. Same row
        shape as get_top_buys (net flow, fund counts, top 5 parties with activity and confidence). Observed,
        descriptive flows — not recommendations. Use it for "what did funds sell the most".

        Args:
            window_days: Lookback in days; omit for the market's own window (TR 30, US 100).
            limit: Max rows (default 10).
        """
        return _moves("sells", window_days, limit)

    def get_new_positions(window_days: int | None = None, limit: int = 10) -> str:
        """Stocks that at least one fund/institution newly entered in the window (held nothing before),
        ordered by the number of entering parties, then net flow. Parties are only the entrants (activity
        NEW). Observed, descriptive flows — not recommendations. Use it for "which funds opened new positions".

        Args:
            window_days: Lookback in days; omit for the market's own window (TR 30, US 100).
            limit: Max rows (default 10).
        """
        return _moves("new", window_days, limit)

    def get_sold_out_positions(window_days: int | None = None, limit: int = 10) -> str:
        """Stocks that at least one fund/institution fully exited in the window (holds nothing now), ordered
        by the number of exiting parties, then net flow. Parties are only the leavers (activity EXIT).
        Observed, descriptive flows — not recommendations. Use it for "which funds closed positions".

        Args:
            window_days: Lookback in days; omit for the market's own window (TR 30, US 100).
            limit: Max rows (default 10).
        """
        return _moves("exits", window_days, limit)

    def get_fund_holdings(code: str) -> str:
        """A fund's latest reported portfolio: as_of (report date), total_value, and every holding with
        symbol, quantity, market_value and weight_pct, largest first. A snapshot, not activity — pair it
        with get_fund_holding_changes for what changed. Reported figures, not recommendations.

        Args:
            code: Fund code, e.g. TMV (US: CIK<number>).
        """
        data = analytics.fund_detail(session, code)
        if data is None:
            return _dump({"error": f"unknown fund {code}"})
        return _dump({"code": data["code"], "name": data["name"], "as_of": data["snapshot_as_of"],
                      "total_value": data["total_value"], "holdings": data["holdings"]})

    def get_fund_holding_changes(code: str, window_days: int | None = None, kind: analytics.MoveKind = "buys") -> str:
        """One fund's own moves in the window (payload: fund, as_of, window_start, total, rows), one row per
        stock with net_flow_value / net_qty and the fund as the row's single party: kind "buys" = stocks it
        added to or entered (largest net flow first), "sells" = reduced or exited, "new" = entered, "exits" =
        fully sold. That party entry (rows[i].parties[0]) carries delta_qty, delta_value, to_weight_pct,
        delta_weight_pct, period_end and confidence (INFERRED from two reports, EXACT from a disclosed
        transaction). Observed, descriptive flows — not recommendations.

        Args:
            code: Fund code, e.g. TMV (US: CIK<number>).
            window_days: Lookback in days; omit for the market's own window (TR 30, US 100).
            kind: buys | sells | new | exits (default buys).
        """
        return _moves(kind, window_days, 25, code)

    return [get_radar, get_stock, get_fund, list_events, screen_stocks, get_news,
            get_top_buys, get_top_sells, get_new_positions, get_sold_out_positions, get_fund_holdings, get_fund_holding_changes]
