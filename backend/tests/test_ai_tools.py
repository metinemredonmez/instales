"""The AI layer is only as good as its tools. These run without any model call."""

import json

from anthropic import beta_tool

from instilens.ai.tools import build_tools
from instilens.services import analytics


def test_tools_have_schemas_and_return_json(session, pipeline_run):
    tools = {fn.__name__: beta_tool(fn) for fn in build_tools(session)}
    assert set(tools) == {"get_radar", "get_stock", "get_fund", "list_events", "screen_stocks", "get_news"}
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
