"""Research engine contract. The AI layer is *swappable*: Claude API today, a local model later.

Whatever the provider, the rules are the same (see PROMPT in prompts.py):
- the model may only state numbers it obtained from a tool call in this conversation;
- every answer carries the tool-call audit trail so the UI can show "where did this come from";
- no investment advice — descriptive, not prescriptive.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ToolCall:
    name: str
    input: dict
    output_preview: str


@dataclass
class ResearchAnswer:
    question: str
    answer: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    usage: dict = field(default_factory=dict)


class ResearchEngine(Protocol):
    name: str

    def ask(self, question: str, market: str = "TR") -> ResearchAnswer: ...
