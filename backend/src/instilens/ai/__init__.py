from sqlalchemy.orm import Session

from instilens.ai.engine import ResearchAnswer, ResearchEngine, ToolCall
from instilens.config import settings


def build_engine(session: Session) -> ResearchEngine:
    if settings.ai_provider == "local":
        from instilens.ai.local_engine import LocalResearchEngine

        return LocalResearchEngine(session, settings.ai_local_base_url, settings.ai_local_model, settings.ai_local_api_key,
                                   deadline=settings.ai_local_deadline_s, prompt_chars=settings.ai_local_prompt_chars)
    from instilens.ai.claude_engine import ClaudeResearchEngine

    return ClaudeResearchEngine(session, model=settings.ai_model)


__all__ = ["ResearchAnswer", "ResearchEngine", "ToolCall", "build_engine"]
