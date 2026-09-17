"""Research engine contract. The AI layer is *swappable*: the Claude API (claude_engine) or a self-hosted
OpenAI-compatible model (local_engine), chosen by settings.ai_provider.

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
    # Figures (three or more digits, as the answer spells them) the answer states but no tool result carries at the
    # start of a number; figures under 100 and bare four-digit years are not checked (see local_engine). The local
    # engine checks every answer and prefixes it with a sentence naming them; the Claude engine relies on the prompt
    # and leaves this empty. Never dropped silently: the Research page shows the list in a warning band above the
    # answer, next to the audit trail.
    unverified_numbers: list[str] = field(default_factory=list)


class ResearchEngine(Protocol):
    name: str

    def ask(self, question: str, market: str = "TR") -> ResearchAnswer: ...
