"""Claude-assisted structuring of KAP filings whose numbers live in prose or in a PDF (no table).

Safety net: every nominal the model returns must literally appear in the source text (with Turkish
thousand separators stripped), every date must parse, and sides must be ALIS/SATIS. Anything else is
dropped. So the model can only *find* numbers, never invent them.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Literal

import anthropic
from pydantic import BaseModel, Field

from instilens.config import settings


class TxRow(BaseModel):
    transaction_date: str = Field(description="dd.mm.yyyy")
    side: Literal["ALIS", "SATIS"]
    nominal: int = Field(gt=0, description="adet / nominal TL, integer")
    price: str | None = Field(None, description="average price if stated, decimal with dot")
    ownership_before_pct: str | None = None
    ownership_after_pct: str | None = None


class TxExtraction(BaseModel):
    rows: list[TxRow]
    fund_codes: list[str] = Field(default_factory=list)
    subject_symbol: str | None = None


SYSTEM = """You extract share purchase/sale figures from Turkish KAP 'Pay Alım Satım Bildirimi' text.
Return only what is explicitly stated: each transaction date, side (ALIS = alış/alım, SATIS = satış/satım),
nominal amount (adet / nominal TL as an integer), average price if given, ownership ratios before/after
(as numbers, without %). Fund codes are 3-letter TEFAS codes mentioned as fon kodu; subject_symbol is the
BIST ticker in parentheses after the company name. Never estimate or compute; if a figure is absent, omit it."""


def validate(extraction: TxExtraction, source_text: str) -> list[dict]:
    """Keep only rows whose nominal literally occurs in the text (with . , and spaces removed)."""
    flat = re.sub(r"[.\s]", "", source_text)
    out = []
    for r in extraction.rows:
        try:
            d = datetime.strptime(r.transaction_date.replace("/", "."), "%d.%m.%Y").date()
        except ValueError:
            continue
        if str(r.nominal) not in flat or d > date.today():
            continue
        out.append({"transaction_date": d.isoformat(), "side": r.side, "nominal": r.nominal, "price": r.price, "before": r.ownership_before_pct, "after": r.ownership_after_pct})
    return out


def extract_transactions(text: str) -> tuple[list[dict], list[str], str | None]:
    if not settings.anthropic_api_key or len(text.strip()) < 40:
        return [], [], None
    client = anthropic.Anthropic(api_key=settings.anthropic_api_key)
    r = client.messages.parse(
        model=settings.ai_model, max_tokens=4000, thinking={"type": "adaptive"}, output_config={"effort": "low"},
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": text[:12000]}],
        output_format=TxExtraction,
    )
    if r.stop_reason == "refusal" or r.parsed_output is None:
        return [], [], None
    return validate(r.parsed_output, text), [c.upper() for c in r.parsed_output.fund_codes if len(c) == 3], (r.parsed_output.subject_symbol or None)
