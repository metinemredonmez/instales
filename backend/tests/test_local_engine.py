"""The local (OpenAI-compatible) research engine, without a model: function schemas generated from the real tool
callables, the tool loop behind an httpx.MockTransport that plays the server, retries and the deadline, the prompt
budget, the figure check that flags what no tool result carries, and the round budget. No network anywhere."""

import json

import httpx
import pytest

from instilens.ai import build_engine, local_engine
from instilens.ai.assess import AiUnavailable
from instilens.ai.local_engine import (
    MAX_ROUNDS,
    LocalResearchEngine,
    completions_url,
    is_turkish,
    unverified_numbers,
)
from instilens.ai.openai_tools import function_schema, json_schema, parse_docstring
from instilens.ai.prompts import SYSTEM_PROMPT
from instilens.ai.tools import build_tools
from instilens.config import settings

# --- schemas -----------------------------------------------------------------------------------


def test_function_schemas_from_the_real_tools(session):
    schemas = {s["function"]["name"]: s["function"] for s in (function_schema(fn) for fn in build_tools(session))}
    assert set(schemas) == {fn.__name__ for fn in build_tools(session)} and len(schemas) == 22
    assert all(s["description"] and s["parameters"]["type"] == "object" for s in schemas.values())
    assert all(schema["type"] == "function" for schema in (function_schema(fn) for fn in build_tools(session)))

    screener = schemas["screen_stocks"]["parameters"]
    assert screener["required"] == [] and screener["properties"]["min_funds_new"] == {"type": "integer", "description": "Minimum number of funds that opened a new position."}
    assert screener["properties"]["max_price_change_30d_pct"]["type"] == "number"
    assert screener["properties"]["signal_types"]["type"] == "array" and "INSIDER_BUY_CLUSTER" in screener["properties"]["signal_types"]["items"]["enum"]
    changes = schemas["get_fund_holding_changes"]["parameters"]
    assert changes["required"] == ["code"] and changes["properties"]["kind"] == {"type": "string", "enum": ["buys", "sells", "new", "exits"], "description": "buys | sells | new | exits (default buys)."}
    assert changes["properties"]["window_days"]["type"] == "integer"  # `int | None` → integer, optional
    overlap = schemas["get_fund_overlap"]["parameters"]
    assert overlap["required"] == ["codes"] and overlap["properties"]["codes"]["type"] == "array" and overlap["properties"]["codes"]["items"] == {"type": "string"}
    assert schemas["get_filings"]["parameters"]["properties"]["form"]["enum"] == ["8-K", "10-K", "10-Q", "4", "4/A"]
    assert schemas["get_radar"]["parameters"] == {"type": "object", "properties": {"limit": {"type": "integer", "description": "Max rows per list (default 10)."}}, "required": []}
    # Multi-line docstrings: the description is the text before Args, an argument keeps its continuation lines.
    assert schemas["get_top_buys"]["description"].startswith("Stocks with the largest positive net institutional flow") and "\n" not in schemas["get_top_buys"]["description"]
    assert schemas["get_insider_trades"]["parameters"]["properties"]["days"]["description"] == "Lookback in days (30-730, default 90)."


def test_docstring_and_type_mapping():
    description, args = parse_docstring("""Does a thing.
        Over two lines.

        Args:
            symbol: Ticker, e.g. ASELS.
            limit (int): Max rows
                continued here.
            note: One with a colon: inside.

        Returns:
            text
        """)
    assert description == "Does a thing. Over two lines."
    assert args == {"symbol": "Ticker, e.g. ASELS.", "limit": "Max rows continued here.", "note": "One with a colon: inside."}
    assert parse_docstring(None) == ("", {}) and parse_docstring("Only a line.") == ("Only a line.", {})
    from typing import Literal

    assert json_schema(str) == {"type": "string"} and json_schema(float | None) == {"type": "number"} and json_schema(bool) == {"type": "boolean"}
    assert json_schema(list[Literal["a", "b"]] | None) == {"type": "array", "items": {"type": "string", "enum": ["a", "b"]}}
    assert json_schema(Literal[1, 2]) == {"type": "integer", "enum": [1, 2]} and json_schema(dict) == {"type": "object"} and json_schema(object) == {}
    assert json_schema(int | str) == {"anyOf": [{"type": "integer"}, {"type": "string"}]}

    def plain(a, b=1):
        pass

    assert function_schema(plain)["function"] == {"name": "plain", "description": "", "parameters": {"type": "object", "properties": {"a": {}, "b": {}}, "required": ["a"]}}


# --- a fake OpenAI-compatible server --------------------------------------------------------------


class _Server:
    """Plays /v1/chat/completions: `script` is a list of responses, one per request, each either a list of
    (tool name, arguments) pairs to call or a string to answer with (a callable gets the request body and the tool
    results seen so far and returns the answer). `statuses` forces HTTP codes for the first requests; `ids=False`
    plays a server that omits tool-call ids. Every request body is kept for the assertions, with its headers and
    the timeout the client asked for."""

    def __init__(self, script, statuses=(), ids=True):
        self.script, self.statuses, self.ids = list(script), list(statuses), ids
        self.bodies: list[dict] = []
        self.client = httpx.Client(transport=httpx.MockTransport(self))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        self.bodies.append({**body, "_headers": dict(request.headers), "_timeout": request.extensions.get("timeout")})
        if self.statuses:
            code = self.statuses.pop(0)
            if code != 200:
                return httpx.Response(code, json={"error": {"message": f"forced {code}"}}, request=request)
        step = self.script.pop(0) if self.script else "no more script"
        results = [m["content"] for m in body["messages"] if m["role"] == "tool"]
        if callable(step):
            step = step(body, results)
        if isinstance(step, str):
            message = {"role": "assistant", "content": step}
        else:
            message = {"role": "assistant", "content": None,
                       "tool_calls": [{**({"id": f"call_{i}"} if self.ids else {}), "type": "function", "function": {"name": name, "arguments": args if isinstance(args, str) else json.dumps(args)}}
                                      for i, (name, args) in enumerate(step)]}
        return httpx.Response(200, json={"id": "x", "model": "qwen2.5:14b", "choices": [{"index": 0, "message": message, "finish_reason": "tool_calls" if message.get("tool_calls") else "stop"}],
                                         "usage": {"prompt_tokens": 100, "completion_tokens": 10}}, request=request)


def _engine(session, server: _Server, **kw) -> LocalResearchEngine:
    naps: list[float] = []
    engine = LocalResearchEngine(session, "http://127.0.0.1:11434", "qwen2.5:14b", client=server.client, sleep=naps.append, **kw)
    engine.naps = naps  # type: ignore[attr-defined]
    return engine


def test_tool_loop_runs_tools_and_audits_them(session, pipeline_run):
    def answer(body, results):
        stock = json.loads(results[1])
        return f"THYAO leads the radar; ASELS Smart Money Score {stock['scores']['SMART_MONEY']['score']} with {len(stock['top_buyers'])} top buyers (as of {stock['as_of']})."

    server = _Server([[("get_radar", {"limit": 3}), ("get_stock", {"symbol": "ASELS"})], answer])
    out = _engine(session, server).ask("What are funds doing in ASELS?", "TR")
    assert out.model == "qwen2.5:14b" and out.usage == {"input_tokens": 200, "output_tokens": 20} and out.unverified_numbers == []
    assert out.answer.startswith("THYAO leads the radar; ASELS Smart Money Score") and "Unverified" not in out.answer
    assert [(c.name, c.input) for c in out.tool_calls] == [("get_radar", {"limit": 3}), ("get_stock", {"symbol": "ASELS"})]
    assert len(out.tool_calls[0].output_preview) == 301 and out.tool_calls[0].output_preview.endswith("…")  # the audit trail keeps a preview, the model saw it all
    assert out.tool_calls[1].output_preview.startswith('{"symbol": "ASELS"')
    # What the server saw: the system prompt, the tools as function schemas, then the tool results as `tool` messages.
    first, second = server.bodies
    assert first["model"] == "qwen2.5:14b" and first["messages"][0] == {"role": "system", "content": SYSTEM_PROMPT} and first["messages"][1]["content"] == "What are funds doing in ASELS?"
    assert len(first["tools"]) == 22 and first["tool_choice"] == "auto" and first["stream"] is False and "authorization" not in first["_headers"]
    assert [m["role"] for m in second["messages"]] == ["system", "user", "assistant", "tool", "tool"]
    assert second["messages"][2]["tool_calls"][0]["function"]["name"] == "get_radar" and second["messages"][3]["tool_call_id"] == "call_0"
    assert json.loads(second["messages"][3]["content"])["accumulated"][0]["symbol"] == "THYAO" and second["messages"][4]["name"] == "get_stock"
    assert server.script == [] and engine_url_ok()


def engine_url_ok() -> bool:
    return (completions_url("http://127.0.0.1:11434"), completions_url("http://host/v1/"), completions_url("https://gw/v1/chat/completions")) == (
        "http://127.0.0.1:11434/v1/chat/completions", "http://host/v1/chat/completions", "https://gw/v1/chat/completions")


def test_bad_tool_calls_come_back_as_errors_not_crashes(session, pipeline_run):
    server = _Server([[("get_stock", "{not json"), ("no_such_tool", {}), ("get_fund_overlap", {"codes": ["TMV"]}), ("get_radar", {"bogus": 1})], "Done."])
    out = _engine(session, server).ask("test", "TR")
    assert out.answer == "Done." and [c.name for c in out.tool_calls] == ["get_stock", "no_such_tool", "get_fund_overlap", "get_radar"]
    results = [json.loads(m["content"]) for m in server.bodies[1]["messages"] if m["role"] == "tool"]
    assert results[0] == {"error": "arguments are not valid JSON"} and results[1] == {"error": "unknown tool no_such_tool"}
    assert "error" in results[2] and results[3]["error"].startswith("bad arguments for get_radar")
    assert out.tool_calls[0].input == {"_raw": "{not json"}


def test_api_key_and_transport_options(session, pipeline_run):
    server = _Server(["ok"])
    engine = LocalResearchEngine(session, "http://gw/v1", "m", "secret", client=server.client)
    assert engine.ask("hi").answer == "ok" and server.bodies[0]["_headers"]["authorization"] == "Bearer secret"
    with pytest.raises(AiUnavailable):
        LocalResearchEngine(session, "http://gw", "", client=server.client).ask("hi")  # no model configured


def test_retry_on_429_and_5xx_then_give_up(session, pipeline_run):
    server = _Server(["fine"], statuses=[429, 503, 200])
    engine = _engine(session, server)
    assert engine.ask("q").answer == "fine" and engine.naps == [0.5, 1.0] and len(server.bodies) == 3

    server = _Server(["never"], statuses=[500, 500, 500])
    engine = _engine(session, server)
    with pytest.raises(AiUnavailable, match="after 3 tries"):
        engine.ask("q")
    assert len(server.bodies) == 3 and engine.naps == [0.5, 1.0]

    server = _Server(["never"], statuses=[400])
    with pytest.raises(AiUnavailable, match="HTTP 400$"):  # the upstream body is logged, never shown
        _engine(session, server).ask("q")
    assert len(server.bodies) == 1  # a client error is not retried

    failures: list[str] = []

    def refuse(request):
        failures.append("connect")
        raise httpx.ConnectError("connection refused", request=request)

    engine = LocalResearchEngine(session, "http://127.0.0.1:1", "m", client=httpx.Client(transport=httpx.MockTransport(refuse)), sleep=lambda _s: None)
    with pytest.raises(AiUnavailable, match=r"after 3 tries \(ConnectError\)"):
        engine.ask("q")
    assert failures == ["connect"] * 3

    def stall(request):  # the server is still generating: a retry would only queue behind the abandoned request
        failures.append("read")
        raise httpx.ReadTimeout("read timed out", request=request)

    engine = LocalResearchEngine(session, "http://127.0.0.1:1", "m", client=httpx.Client(transport=httpx.MockTransport(stall)), sleep=lambda _s: None)
    with pytest.raises(AiUnavailable, match="ReadTimeout"):
        engine.ask("q")
    assert failures[-2:] == ["connect", "read"]


def test_deadline_bounds_the_whole_ask_and_shrinks_the_call_timeout(session, pipeline_run):
    ticks = [0.0, 250.0, 400.0]  # ask starts; first call 250 s in; second call past the deadline
    server = _Server([[("get_radar", {"limit": 1})], "late"])
    engine = _engine(session, server, deadline=300.0, clock=lambda: ticks.pop(0))
    with pytest.raises(AiUnavailable, match="deadline of 300 s exceeded"):
        engine.ask("q")
    assert len(server.bodies) == 1 and server.bodies[0]["_timeout"]["read"] == 50.0  # the remaining 50 s, not the 120 s default

    ticks = [0.0, 10.0, 20.0]
    server = _Server([[("get_radar", {"limit": 1})], "in time"])
    assert _engine(session, server, deadline=300.0, clock=lambda: ticks.pop(0)).ask("q").answer == "in time"
    assert [b["_timeout"]["read"] for b in server.bodies] == [120.0, 120.0]


def test_missing_tool_call_ids_are_synthesised(session, pipeline_run):
    server = _Server([[("get_radar", {"limit": 1}), ("get_radar", {"limit": 2})], "ok"], ids=False)
    assert _engine(session, server).ask("q").answer == "ok"
    second = server.bodies[1]["messages"]
    assert [tc["id"] for tc in second[2]["tool_calls"]] == ["call_0_0", "call_0_1"] and [m["tool_call_id"] for m in second[3:]] == ["call_0_0", "call_0_1"]


def test_tool_output_is_clipped_for_the_model_and_the_prompt_budget_ends_the_tool_loop(session, pipeline_run, monkeypatch):
    monkeypatch.setattr(local_engine, "TOOL_OUTPUT_MAX", 200)
    server = _Server([[("get_stock", {"symbol": "ASELS"})], "ASELS: 1608500 cited."])
    out = _engine(session, server).ask("q")
    sent = server.bodies[1]["messages"][3]["content"]
    full = out.tool_calls[0].output_preview
    assert sent.startswith(full[:200]) and sent.endswith(" chars]") and "… [truncated, " in sent and len(sent) < 260
    assert out.unverified_numbers == []  # the figure sits past the clip: the check reads the whole result

    always_tools = lambda body, results: [("get_radar", {"limit": 1})] if "tools" in body else "Budget spent."  # noqa: E731
    server = _Server([always_tools] * 4)
    out = _engine(session, server, prompt_chars=len(SYSTEM_PROMPT) + 100).ask("loop", "TR")  # one tool result overruns the budget
    assert len(server.bodies) == 2 and "tools" in server.bodies[0] and "tools" not in server.bodies[1] and out.answer == "Budget spent." and len(out.tool_calls) == 1
    assert local_engine._clip("x" * 10, 10) == "x" * 10 and local_engine._clip("x" * 11, 10) == "x" * 10 + "… [truncated, 11 chars]"


def test_unverified_figures_are_flagged_and_prefixed(session, pipeline_run):
    server = _Server([[("get_stock", {"symbol": "ASELS"})], "ASELS net flow was 843.000.000 TL, 12345 funds increased, score 88.5, KAP #1608500 cited, year 2026."])
    out = _engine(session, server).ask("How many funds increased ASELS?", "TR")
    assert out.unverified_numbers == ["843.000.000", "12345"]  # 843 million is nowhere in the stock payload; 1608500 (the GROUPED sale) is, 2026 is a year
    assert out.answer.startswith("Unverified figures: 843.000.000, 12345 — these values were not found in any tool result.\n\nASELS net flow was")

    server = _Server([[("get_stock", {"symbol": "ASELS"})], "ASELS'te 12345 fon pozisyon artırdı."])
    out = _engine(session, server).ask("ASELS'te kaç fon pozisyon artırdı?", "TR")
    assert out.unverified_numbers == ["12345"] and out.answer.startswith("Doğrulanamayan rakamlar: 12345 — bu değerler hiçbir araç sonucunda bulunamadı.")

    # Figures from the question are not flagged; thousands separators on either side do not break the match.
    server = _Server(["KAP #1608450 is the correction; the notice covers 46.526.835 shares."])
    out = _engine(session, server).ask("What is KAP #1608450 about, 46526835 shares?", "TR")
    assert out.unverified_numbers == [] and not out.answer.startswith("Unverified")
    assert unverified_numbers("46,526,835 and 12.5", ['{"nominal": 46526835}']) == []
    assert unverified_numbers("46526835", ["46.526.835 nominal"]) == [] and unverified_numbers("999 and 12", ["none"]) == ["999"]
    # A figure must start a number in a result: 843 is "843 million" next to 843000000, not a slice of 1608432.
    assert unverified_numbers("843 million", ["843000000"]) == [] and unverified_numbers("843 shares", ["id 1608432"]) == ["843"]
    assert unverified_numbers("price 317.23, then 1,438 shares", ['"price": 317.23, "shares": 1438']) == [] and unverified_numbers("12.500 lots", ["12500"]) == []
    # Bare years pass unchecked; a year-shaped percentage or decimal, and ids of other lengths, do not.
    assert unverified_numbers("up since 2019, versus 2025. Rate 2025% on 20251", ["x"]) == ["2025", "20251"]
    assert unverified_numbers("100% of 250 funds in 2024,5 cases", ["nothing"]) == ["100", "250", "2024"]
    assert is_turkish("Hangi fonlar aldı?") and is_turkish("ASELS son durum ne") and not is_turkish("What did funds buy?") and not is_turkish("ASELS")


def test_round_budget_ends_with_a_closing_call_without_tools(session, pipeline_run):
    always_tools = lambda body, results: [("get_radar", {"limit": 1})] if "tools" in body else "Budget spent, here is what the radar shows."  # noqa: E731
    server = _Server([always_tools] * (MAX_ROUNDS + 1))
    out = _engine(session, server).ask("loop", "TR")
    assert len(server.bodies) == MAX_ROUNDS + 1 and all("tools" in b for b in server.bodies[:-1]) and "tools" not in server.bodies[-1]
    assert len(out.tool_calls) == MAX_ROUNDS and out.answer == "Budget spent, here is what the radar shows."

    server = _Server([always_tools] * 3)
    out = _engine(session, server, max_rounds=2).ask("loop", "TR")
    assert len(server.bodies) == 3 and len(out.tool_calls) == 2

    server = _Server([[("get_radar", {"limit": 1})], [("get_radar", {"limit": 1})], [("get_radar", {"limit": 1})]])
    out = _engine(session, server, max_rounds=2).ask("loop", "TR")  # the model keeps calling tools even without them: the answer says so instead of staying empty
    assert out.answer == "The model produced no answer." and len(out.tool_calls) == 2


def test_build_engine_picks_the_local_engine(session, monkeypatch):
    monkeypatch.setattr(settings, "ai_provider", "local")
    monkeypatch.setattr(settings, "ai_local_model", "llama3.1")
    monkeypatch.setattr(settings, "ai_local_api_key", "k")
    engine = build_engine(session)
    assert isinstance(engine, LocalResearchEngine) and engine.name == "local" and engine.model == "llama3.1" and engine.api_key == "k"
    assert engine.url == "http://127.0.0.1:11434/v1/chat/completions" and engine.client.timeout.read == 120.0
    assert engine.deadline == settings.ai_local_deadline_s == 300 and engine.prompt_chars == settings.ai_local_prompt_chars == 40_000
    monkeypatch.setattr(settings, "ai_provider", "claude")
    assert build_engine(session).name == "claude"
