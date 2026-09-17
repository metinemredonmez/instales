"""The AI layer is only as good as its tools. These run without any model call."""

import json

from anthropic import beta_tool

from instilens.ai.tools import build_tools
from instilens.services import analytics


def test_tools_have_schemas_and_return_json(session, pipeline_run):
    tools = {fn.__name__: beta_tool(fn) for fn in build_tools(session)}
    assert set(tools) == {"get_radar", "get_stock", "get_fund", "list_events", "screen_stocks", "get_news",
                          "get_top_buys", "get_top_sells", "get_new_positions", "get_sold_out_positions", "get_fund_holdings", "get_fund_holding_changes",
                          "get_income_statements", "get_balance_sheets", "get_cash_flow_statements", "get_financial_metrics"}
    schema = tools["screen_stocks"].to_dict()
    assert "max_price_change_30d_pct" in schema["input_schema"]["properties"]
    assert json.loads(tools["get_radar"].call({"limit": 3}))["accumulated"][0]["symbol"] == "THYAO"
    assert json.loads(tools["get_stock"].call({"symbol": "asels"}))["symbol"] == "ASELS"
    assert "error" in json.loads(tools["get_fund"].call({"code": "NOPE"}))


def test_screener_positive_divergence_query(session, pipeline_run):
    rows = analytics.screener(session, "TR", min_funds_increasing=3, max_price_change_pct=0)
    assert [r["symbol"] for r in rows] == ["ASELS"]
    assert rows[0]["price_change_30d_pct"] < 0 and "POSITIVE_DIVERGENCE" in rows[0]["signals"]
    assert analytics.screener(session, "TR", signal_types=["EXIT_CLUSTER"])[0]["symbol"] == "SASA"
    assert analytics.screener(session, "TR", min_smart_money_score=99) == []


def test_move_tools_return_ranked_rows_with_parties(session, pipeline_run):
    tools = {fn.__name__: beta_tool(fn) for fn in build_tools(session)}
    row_keys = {"symbol", "name", "net_flow_value", "net_qty", "funds_increasing", "funds_reducing", "funds_new", "funds_exited", "party_count", "parties"}
    buys = json.loads(tools["get_top_buys"].call({"limit": 2}))
    assert buys["kind"] == "buys" and len(buys["rows"]) == 2 and set(buys["rows"][0]) == row_keys
    assert buys["rows"][0]["symbol"] == "THYAO" and buys["rows"][0]["parties"][0]["code"] == "TMV"
    assert json.loads(tools["get_top_sells"].call({}))["rows"][0]["symbol"] == "SASA"
    assert json.loads(tools["get_new_positions"].call({"window_days": 30}))["rows"][0]["funds_new"] == 3
    assert json.loads(tools["get_sold_out_positions"].call({}))["rows"][0]["funds_exited"] == 3
    assert "enum" in tools["get_fund_holding_changes"].to_dict()["input_schema"]["properties"]["kind"]
    for tool in ("get_top_buys", "get_top_sells", "get_new_positions", "get_sold_out_positions", "get_fund_holdings", "get_fund_holding_changes"):
        assert "not recommendations" in tools[tool].to_dict()["description"]

    holdings = json.loads(tools["get_fund_holdings"].call({"code": "tmv"}))
    assert set(holdings) == {"code", "name", "as_of", "total_value", "holdings"} and holdings["as_of"] == "2026-08-31"
    assert set(holdings["holdings"][0]) == {"symbol", "quantity", "market_value", "weight_pct"} and holdings["holdings"][0]["symbol"] == "ASELS"
    changes = json.loads(tools["get_fund_holding_changes"].call({"code": "TMV", "kind": "sells"}))
    assert changes["fund"]["code"] == "TMV" and [r["symbol"] for r in changes["rows"]] == ["SASA", "EREGL"]
    assert changes["rows"][0]["parties"] == [changes["rows"][0]["parties"][0]] and changes["rows"][0]["parties"][0]["code"] == "TMV"
    assert "error" in json.loads(tools["get_fund_holdings"].call({"code": "NOPE"}))
    assert "error" in json.loads(tools["get_fund_holding_changes"].call({"code": "NOPE"}))
