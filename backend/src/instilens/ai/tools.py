"""Provider-agnostic tool functions. Each returns a compact JSON string built from our own tables.

Bound to a SQLAlchemy session via `build_tools(session)`; the Claude engine wraps them with
`@beta_tool`, a local engine would expose the same callables through its own tool protocol.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Literal

from sqlalchemy.orm import Session

from instilens.services import analytics, fundamentals, insiders, ownership

PeriodName = Literal["annual", "quarterly"]
FilingForm = Literal["8-K", "10-K", "10-Q", "4", "4/A"]

SignalName = Literal[
    "ACCUMULATION",
    "DISTRIBUTION",
    "POSITIVE_DIVERGENCE",
    "NEGATIVE_DIVERGENCE",
    "NEW_POSITION_CLUSTER",
    "EXIT_CLUSTER",
    "INSIDER_BUY_CLUSTER",
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

    def _statements(symbol: str, kind: str, period: str, limit: int) -> str:
        data = fundamentals.stock_fundamentals(session, market, symbol, period)  # type: ignore[arg-type]  # PeriodName is validated by the tool schema
        if data is None:
            return _dump({"error": f"unknown symbol {symbol}"})
        return _dump({"symbol": data["symbol"], "name": data["name"], "currency": data["currency"], "source": data["source"],
                      "fetched_at": data["fetched_at"], "period": data["period"],
                      "statements": data["statements"][kind][: max(1, min(limit, fundamentals.MAX_STATEMENTS))]})

    def get_income_statements(symbol: str, period: PeriodName = "annual", limit: int = 4) -> str:
        """A company's reported income statements — revenue, cost of revenue, gross profit, operating income,
        EBITDA, pretax income, net income, diluted EPS, interest expense — one per reporting period, newest
        first; useful for describing profitability and operating efficiency over time. Reported figures from
        the fundamentals provider, not estimates: the payload carries currency, source and fetched_at, each
        statement its period_end (cite it with every number), and a line the filing did not report is null.
        Descriptive only — never turn these into a rating or a recommendation.

        Args:
            symbol: Ticker symbol, e.g. ASELS.
            period: annual | quarterly (default annual).
            limit: Max statements, newest first (default 4, at most 8).
        """
        return _statements(symbol, "income", period, limit)

    def get_balance_sheets(symbol: str, period: PeriodName = "annual", limit: int = 4) -> str:
        """A company's reported balance sheets — total assets, total liabilities, equity, total debt, cash,
        current assets, current liabilities — each a snapshot of its financial position at a period end,
        newest first. Reported figures from the fundamentals provider: the payload carries currency, source and
        fetched_at, each sheet its period_end (cite it with every number), and a line the filing did not report
        is null. Descriptive only — never turn these into a rating or a recommendation.

        Args:
            symbol: Ticker symbol, e.g. ASELS.
            period: annual | quarterly (default annual).
            limit: Max sheets, newest first (default 4, at most 8).
        """
        return _statements(symbol, "balance", period, limit)

    def get_cash_flow_statements(symbol: str, period: PeriodName = "annual", limit: int = 4) -> str:
        """A company's reported cash flow statements — operating cash flow, capital expenditure (negative),
        free cash flow, dividends paid, share repurchases — showing how cash was generated and used per
        reporting period, newest first; useful for describing liquidity. Reported figures from the
        fundamentals provider: the payload carries currency, source and fetched_at, each statement its
        period_end (cite it with every number), and a line the filing did not report is null (free cash flow
        is operating cash flow + capex when the provider prints both but not the total). Descriptive only —
        never turn these into a rating or a recommendation.

        Args:
            symbol: Ticker symbol, e.g. ASELS.
            period: annual | quarterly (default annual).
            limit: Max statements, newest first (default 4, at most 8).
        """
        return _statements(symbol, "cashflow", period, limit)

    def get_financial_metrics(symbol: str) -> str:
        """The current financial metrics snapshot of a company — market cap, enterprise value, trailing and
        forward P/E, price/book, price/sales, EV/EBITDA, margins, ROA/ROE, trailing revenue / EBITDA / net
        income / EPS, dividend yield, payout ratio, beta, 52-week range, shares outstanding, float, short
        interest — plus ratios derived from its newest annual statements (gross / operating / net / FCF
        margin, debt-to-equity, revenue and net income growth year over year, with the period_end they come
        from). Figures as the fundamentals provider states them on `snapshot.as_of` (cite it), with source and
        fetched_at. Two currencies: market_cap, enterprise_value, the 52-week range and eps_ttm are in
        `snapshot.quote_currency` (the listing currency); revenue_ttm / ebitda_ttm / net_income_ttm and the
        derived ratios' statements are in `currency` (the reporting currency) — THYAO trades in TRY and reports
        in USD, so never state its market cap in dollars. Percentages are already ×100; forward_pe is the one
        figure based on consensus estimates rather than reported numbers, say so when citing it; anything not
        stated is null, and `snapshot` itself is null until the symbol has been fetched. Descriptive only —
        never turn a ratio into a verdict or a recommendation.

        Args:
            symbol: Ticker symbol, e.g. ASELS.
        """
        data = fundamentals.stock_fundamentals(session, market, symbol, "annual")
        if data is None:
            return _dump({"error": f"unknown symbol {symbol}"})
        return _dump({k: data[k] for k in ("symbol", "name", "currency", "source", "fetched_at", "snapshot", "derived")})

    def get_insider_trades(symbol: str, days: int = 90, limit: int = 30) -> str:
        """Insider transactions of a US company as reported to the SEC on Form 4 over the last `days`: a summary
        (buyers / sellers = distinct insiders with open-market purchases / sales, their values, the 30-day purchase
        cluster if any) and the reported rows, newest first. Each row carries the transaction code AS FILED and you
        must state its meaning instead of calling every acquisition a "buy": P = open-market or private purchase
        (the insider paid market price), S = open-market or private sale, A = grant or award, M = option exercise or
        RSU settlement (shares received under a plan, not bought), F = shares withheld to cover tax on a vesting or
        exercise (not a sale in the market), G = gift, D = disposition to the issuer, C = conversion, X = exercise
        of an in-the-money derivative, J = other (see the filing's footnotes); `derivative: true` rows come from the
        derivative table (options, RSUs). `price` is the price the filing states (null when it states none — never
        estimate it), `value` = shares × price, `role` the insider's relationship (director, officer,
        ten_percent_owner, other) and `title` the officer title as filed. Every row names its EDGAR accession and
        filing URL — cite them. `fetched_at` is when EDGAR was last read for this issuer; null together with an
        empty `transactions` means the issuer has not been read yet — say so, never "no insider activity".
        `truncated: true` means the window holds more rows than returned (`edgar_url` lists them all); `supported:
        false` means the symbol is not a US issuer (no Form 4 data). These are reported facts with EDGAR accession
        links, not recommendations; never turn a purchase or a cluster into a verdict.

        Args:
            symbol: US ticker, e.g. AAPL.
            days: Lookback in days (30-730, default 90).
            limit: Max transaction rows, newest first (default 30, at most 200).
        """
        days = max(30, min(730, days))
        data = insiders.stock_insiders(session, market, symbol, days)
        if data is None:
            return _dump({"error": f"unknown symbol {symbol}"})
        rows = data["transactions"][: max(1, min(limit, insiders.MAX_TRANSACTIONS))]
        return _dump({**data, "transactions": rows, "truncated": data["truncated"] or len(rows) < len(data["transactions"])})

    def get_filings(symbol: str, form: FilingForm | None = None, limit: int = 10) -> str:
        """A US issuer's recent EDGAR filings, newest first: Form 4 (insider transactions), 8-K (current report —
        `items` lists the item codes, e.g. 2.02 = results of operations, 5.02 = officer/director changes, 1.01 =
        material agreement, 8.01 = other events), 10-K (annual report) and 10-Q (quarterly report), each with its
        filing date, the period it reports on, the accession, the filing index `url` and the `primary_url` of the
        main document. Pass `form` to keep one type. `supported: false` means the symbol is not a US issuer;
        `fetched_at` null with an empty list means the issuer has not been read yet, not that nothing was filed. An
        index of what was filed when — cite the accession or URL; the documents' contents are not included.

        Args:
            symbol: US ticker, e.g. AAPL.
            form: 8-K | 10-K | 10-Q | 4 | 4/A; omit for every form.
            limit: Max filings, newest first (default 10, at most 100).
        """
        data = insiders.stock_filings(session, market, symbol, form, max(1, min(limit, 100)))
        return _dump(data if data is not None else {"error": f"unknown symbol {symbol}"})

    def get_stock_ownership(symbol: str, limit: int = 15) -> str:
        """Who holds a stock according to the funds' LATEST portfolio reports: `totals` (holders, institutions, held
        quantity, Σ reported market value, pct_of_shares = held quantity / shares outstanding × 100 — null until the
        share count is known — top10_pct_of_held and hhi, the Herfindahl index of holder quantities 0..10000, for how
        concentrated the holding is), the `limit` largest holders (fund, institution, quantity, market_value, weight_pct
        inside that fund, pct_of_shares, the report's as_of, last_move NEW/ADD/REDUCE/HOLD with its period end,
        confidence) and `crowding` (the CROWDING score with level low/medium/high and its per-component `why`; null
        until computed). One row per fund, never two dates of one fund; reports older than two reporting periods
        (TR 60 days, US 182) are left out and counted in `stale_holders` — say so when it is not zero. Reported
        positions, not recommendations: "12 funds hold it, the ten largest hold 80 % of what funds hold" is the
        whole story, never "crowded therefore sell".

        Args:
            symbol: Ticker symbol, e.g. ASELS.
            limit: Max holder rows, largest first (default 15).
        """
        data = ownership.stock_ownership(session, market, symbol, max(1, min(limit, 500)))
        return _dump(data if data is not None else {"error": f"unknown symbol {symbol}"})

    def get_fund_overlap(codes: list[str]) -> str:
        """How far two to six funds' latest books overlap. `pairwise`: for every pair, overlap_pct_symbols (symbols both
        hold / symbols either holds × 100) and overlap_pct_weighted (Σ min(weight_a, weight_b) over the common
        symbols, in percentage points of a book — null when a fund reports no weight for one of them). `common_all`:
        the symbols every requested fund holds, each with every fund's weight, ordered by the smallest weight any fund
        gives the symbol, largest first. `funds` carries each fund's report date (as_of) and holding count — cite the
        dates, the books can be of different months. All funds must be in the current market (a fund of another
        market, or a mixed request, returns an error). Reported holdings, not recommendations.

        Args:
            codes: 2 to 6 fund codes, e.g. ["TMV", "MAC"] (US: CIK<number>).
        """
        try:
            data = ownership.fund_overlap(session, list(codes), market)
        except ownership.OverlapRequestError as exc:
            return _dump({"error": str(exc)})
        return _dump(data if data is not None else {"error": "unknown fund code among " + ", ".join(codes)})

    def get_crowding_score(symbol: str) -> str:
        """The CROWDING score of a stock (0..100, level low < 35 / medium 35..65 / high > 65) with its explanation and
        the ownership totals behind it. It describes how many funds hold the stock and how concentrated the holding
        is — 35 % holder count (25 holders = full), 25 % share of the company held (30 % = full; skipped and the rest
        re-weighted while the share count is unknown, `why.held_pct.skipped` says so), 20 % spread among holders
        (1 − HHI/10000), 20 % net breadth momentum of the score window (funds increasing − reducing, +5 = full) —
        computed from the funds' latest reports, not a valuation view: "crowded" means many funds hold it, never
        "overbought" or a verdict. `crowding` is null until the pipeline has computed the day's scores.

        Args:
            symbol: Ticker symbol, e.g. ASELS.
        """
        data = ownership.stock_ownership(session, market, symbol, limit=1)
        if data is None:
            return _dump({"error": f"unknown symbol {symbol}"})
        return _dump({k: data[k] for k in ("symbol", "name", "market", "as_of", "shares_outstanding", "totals", "crowding", "stale_holders")})

    return [get_radar, get_stock, get_fund, list_events, screen_stocks, get_news,
            get_top_buys, get_top_sells, get_new_positions, get_sold_out_positions, get_fund_holdings, get_fund_holding_changes,
            get_income_statements, get_balance_sheets, get_cash_flow_statements, get_financial_metrics,
            get_insider_trades, get_filings,
            get_stock_ownership, get_fund_overlap, get_crowding_score]
