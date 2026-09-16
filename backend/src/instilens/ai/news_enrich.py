"""AI pass over headlines (replaces Newsomatic's TextRazor/TLDRThis/translate stack with one Claude call).

Input: up to 40 untagged headlines. Output per headline (structured, validated): a one-line Turkish
summary, affected instruments (from the known symbol list only), sector, sentiment. Headline-only —
we never fetch or store article bodies. Cost is a few thousand tokens per batch.
"""

from __future__ import annotations

import json
from typing import Literal

import anthropic
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.ai.assess import AiUnavailable
from instilens.config import settings
from instilens.domain.models import Instrument, NewsItem


class HeadlineTag(BaseModel):
    id: int
    summary_tr: str = Field(max_length=200)
    symbols: list[str] = Field(default_factory=list, max_length=5)
    sector: str = Field("", max_length=40)
    sentiment: Literal["positive", "negative", "neutral"] = "neutral"
    relevance: int = Field(0, ge=0, le=100, description="0 = irrelevant to markets, 100 = market-moving")


class HeadlineBatch(BaseModel):
    items: list[HeadlineTag]


SYSTEM = """You tag financial news headlines for a smart-money analytics product. For each headline return:
summary_tr (one short Turkish sentence, factual, no advice), symbols (ONLY tickers from the provided list that the
headline is clearly about — otherwise empty), sector (short Turkish label), sentiment for the mentioned companies/market,
relevance 0-100 for equity investors. Headlines only; do not invent facts beyond the headline."""


def enrich(session: Session, market: str, limit: int = 40, model: str | None = None) -> int:
    if not settings.anthropic_api_key:
        return 0
    items = session.scalars(
        select(NewsItem).where(NewsItem.market_code == market, NewsItem.ai.is_(None)).order_by(NewsItem.published_at.desc()).limit(limit)
    ).all()
    if not items:
        return 0
    known = sorted({i.symbol for i in session.scalars(select(Instrument).where(Instrument.market_code == market)) if i.symbol.isalpha()})
    payload = [{"id": n.id, "source": n.source, "title": n.title} for n in items]
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    try:
        response = client.messages.parse(
            model=model or settings.ai_news_model,
            max_tokens=8000,
            # Haiku 4.5 takes an explicit thinking budget; adaptive thinking and output_config.effort are
            # 4.6+ parameters and 400 on this model (they only worked while the call used ai_model).
            thinking={"type": "enabled", "budget_tokens": 2000},
            system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
            messages=[{"role": "user", "content": f"Known tickers ({market}): {', '.join(known[:400])}\n\nHeadlines:\n{json.dumps(payload, ensure_ascii=False)}"}],
            output_format=HeadlineBatch,
        )
    except anthropic.APIError as exc:
        raise AiUnavailable(f"{type(exc).__name__}: {getattr(exc, 'message', exc)}") from exc
    except ValidationError as exc:  # a summary over 200 chars etc.; the batch stays untagged and is retried next pull
        raise AiUnavailable(f"model output rejected: {exc.error_count()} field(s) outside the schema") from exc
    if response.stop_reason == "refusal" or response.parsed_output is None:
        return 0
    by_id = {n.id: n for n in items}
    done = 0
    for tag in response.parsed_output.items:
        n = by_id.get(tag.id)
        if n is None:
            continue
        syms = sorted(set(n.symbols or []) | {s.upper() for s in tag.symbols if s.upper() in known})
        n.symbols = syms
        n.ai = {"summary_tr": tag.summary_tr, "sector": tag.sector, "sentiment": tag.sentiment, "relevance": tag.relevance, "symbols": [s.upper() for s in tag.symbols if s.upper() in known], "model": response.model}
        done += 1
    for n in items:  # mark the rest so we don't retry them forever
        if n.ai is None:
            n.ai = {"summary_tr": "", "sector": "", "sentiment": "neutral", "relevance": 0, "model": response.model}
    session.flush()
    return done
