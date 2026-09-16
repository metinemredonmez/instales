"""Claude-backed research engine (Anthropic SDK tool runner).

Model: claude-opus-5 with adaptive thinking. The SDK drives the tool loop; we mirror every tool
call into the answer's audit trail so the UI can show provenance.
"""

from __future__ import annotations

import json

import anthropic
from anthropic import beta_tool
from sqlalchemy.orm import Session

from instilens.ai.engine import ResearchAnswer, ToolCall
from instilens.ai.prompts import SYSTEM_PROMPT
from instilens.ai.tools import build_tools
from instilens.config import settings

MODEL = "claude-opus-5"


class ClaudeResearchEngine:
    name = "claude"

    def __init__(self, session: Session, client: anthropic.Anthropic | None = None, model: str = MODEL) -> None:
        self.session = session
        self.client = client or anthropic.Anthropic(api_key=settings.anthropic_api_key)  # None → SDK env/profile lookup
        self.model = model

    def ask(self, question: str, market: str = "TR") -> ResearchAnswer:
        tools = [beta_tool(fn) for fn in build_tools(self.session, market)]
        runner = self.client.beta.messages.tool_runner(
            model=self.model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=[{"type": "text", "text": SYSTEM_PROMPT, "cache_control": {"type": "ephemeral"}}],
            tools=tools,
            messages=[{"role": "user", "content": question}],
        )
        calls: list[ToolCall] = []
        final = None
        usage = {"input_tokens": 0, "output_tokens": 0}
        for message in runner:
            final = message
            usage["input_tokens"] += message.usage.input_tokens
            usage["output_tokens"] += message.usage.output_tokens
            tool_uses = [b for b in message.content if b.type == "tool_use"]
            response = runner.generate_tool_call_response() if tool_uses else None
            results = {}
            if response is not None:
                for block in response["content"]:
                    if block["type"] == "tool_result":
                        results[block["tool_use_id"]] = _preview(block["content"])
            for tu in tool_uses:
                calls.append(ToolCall(name=tu.name, input=dict(tu.input), output_preview=results.get(tu.id, "")))

        if final is None:
            raise RuntimeError("no response from model")
        if final.stop_reason == "refusal":
            text = "The model declined to answer this request."
        else:
            text = "".join(b.text for b in final.content if b.type == "text")
        return ResearchAnswer(question=question, answer=text, tool_calls=calls, model=self.model, usage=usage)


def _preview(content, limit: int = 300) -> str:
    if isinstance(content, list):
        content = "".join(c.get("text", "") for c in content if isinstance(c, dict))
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False, default=str)
    return content[:limit] + ("…" if len(content) > limit else "")
