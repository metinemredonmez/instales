from sqlalchemy.orm import Session

from instilens.ai.engine import ResearchAnswer, ResearchEngine, ToolCall
from instilens.config import settings


def build_engine(session: Session) -> ResearchEngine:
    if settings.ai_provider == "local":
        from instilens.ai.local_engine import LocalResearchEngine

        return LocalResearchEngine(session, settings.ai_local_base_url, settings.ai_local_model)
    from instilens.ai.claude_engine import ClaudeResearchEngine

    return ClaudeResearchEngine(session, model=settings.ai_model)


__all__ = ["ResearchAnswer", "ResearchEngine", "ToolCall", "build_engine"]
