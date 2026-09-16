"""Placeholder for a self-hosted model (phase 3: "kendi lokal AI'ım").

The contract is identical to ClaudeResearchEngine: same tools (`build_tools`), same system prompt,
same ResearchAnswer with an audit trail. Only the transport changes. Implement against the local
runtime you pick (e.g. an Ollama/vLLM server that supports tool calling); keep the ground rules —
numbers only from tools — enforced in the prompt AND by checking that every figure in the answer
appears in a tool result before returning it.
"""

from sqlalchemy.orm import Session

from instilens.ai.engine import ResearchAnswer


class LocalResearchEngine:
    name = "local"

    def __init__(self, session: Session, base_url: str, model: str) -> None:
        self.session = session
        self.base_url = base_url
        self.model = model

    def ask(self, question: str, market: str = "TR") -> ResearchAnswer:
        raise NotImplementedError("local model engine is scheduled for phase 3; use ClaudeResearchEngine")
