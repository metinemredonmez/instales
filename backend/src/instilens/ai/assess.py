"""AI that connects what we already know: fund flows (our tables) × headlines (our news table).

Two products, both cached in `ai_notes` for the day:
  stock_assessment(symbol) — 3-5 sentences: what institutions did, which headlines relate, what to watch.
  daily_brief(market)      — morning note: yesterday's flows, active signals, relevant headlines.
Rules: descriptive not prescriptive; every number comes from the JSON we hand the model; headlines
are referenced by their id so the UI can link them.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

import anthropic
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.models import AiNote
from instilens.services import analytics

SYSTEM = """You are InstiLens' analyst. You write short, factual notes for investors about institutional
(fund) activity in a stock or market, combining the structured data and the headlines you are given.
Rules: (1) use ONLY numbers present in the JSON; (2) never give advice — no "buy", "sell", "will rise"
(Turkish: "al", "sat", "yükselir"); describe and flag what to watch instead; (3) mention data confidence when
it matters (GROUPED = allocation across the funds is unknown, INFERRED = derived from a snapshot diff);
(4) refer to headlines by [n:ID] and disclosures by [kap:ID] so the UI can link them; (5) if the data is
thin, say so in one sentence rather than padding; (6) write in the language requested by the user prompt —
Turkish or English — including `headline`, `highlights`, `watch` and `confidence_note`; (7) `headline` is one
line with the single most important number; `highlights` are 3 short, number-bearing items; `text` is the full
narrative — write it as 3-5 short paragraphs separated by blank lines, not one block."""

LANGS = ("tr", "en")


def norm_lang(lang: str | None) -> str:
    return lang if lang in LANGS else "tr"


class Note(BaseModel):
    headline: str = Field("", max_length=90, description="one punchy line: the single most important flow of the period, with its number")
    highlights: list[str] = Field(default_factory=list, max_length=3, description="up to 3 short items (≤ 80 chars), each with a number from the data")
    text: str = Field(max_length=1800)
    watch: list[str] = Field(default_factory=list, max_length=4, description="what to watch next, short items")
    headline_ids: list[int] = Field(default_factory=list)
    confidence_note: str = Field("", max_length=200)


def _client() -> anthropic.Anthropic | None:
    return anthropic.Anthropic(api_key=settings.anthropic_api_key) if settings.anthropic_api_key else None


class AiUnavailable(RuntimeError):
    """The model call failed (bad key, quota, outage). Routes turn this into a 503 instead of a 500."""


def _write(session: Session, kind: str, market: str, subject: str, day: date, prompt: str, data: dict, lang: str = "tr") -> AiNote | None:
    client = _client()
    if client is None:
        return None
    prompt = f"Language: {'English' if lang == 'en' else 'Turkish'}.\n{prompt}"
    try:
        r = _parse(client, prompt, data)
    except anthropic.APIError as exc:
        raise AiUnavailable(f"{type(exc).__name__}: {getattr(exc, 'message', exc)}") from exc
    if r.stop_reason == "refusal" or r.parsed_output is None:
        return None
    note = _cached(session, kind, market, subject, day, lang)
    payload = {"headline": r.parsed_output.headline, "highlights": r.parsed_output.highlights, "watch": r.parsed_output.watch,
               "headline_ids": r.parsed_output.headline_ids, "confidence_note": r.parsed_output.confidence_note, "inputs": data}
    if note is None:
        note = AiNote(kind=kind, market_code=market, subject=subject, as_of=day, lang=lang, content=r.parsed_output.text, data=payload, model=r.model)
        session.add(note)
    else:
        note.content, note.data, note.model, note.created_at = r.parsed_output.text, payload, r.model, datetime.now(UTC)
    session.flush()
    return note


def _parse(client: anthropic.Anthropic, prompt: str, data: dict):
    return client.messages.parse(
        model=settings.ai_model, max_tokens=4000, thinking={"type": "adaptive"}, output_config={"effort": "medium"},
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        messages=[{"role": "user", "content": prompt + "\n\nDATA:\n" + json.dumps(data, ensure_ascii=False, default=str)}],
        output_format=Note,
    )


def _cached(session: Session, kind: str, market: str, subject: str, day: date, lang: str = "tr") -> AiNote | None:
    return session.scalar(select(AiNote).where(AiNote.kind == kind, AiNote.market_code == market, AiNote.subject == subject, AiNote.as_of == day, AiNote.lang == lang))


def stock_assessment(session: Session, market: str, symbol: str, day: date | None = None, force: bool = False, lang: str = "tr") -> AiNote | None:
    day, lang = day or date.today(), norm_lang(lang)
    if not force and (c := _cached(session, "STOCK_ASSESSMENT", market, symbol.upper(), day, lang)):
        return c
    detail = analytics.stock_detail(session, market, symbol)
    if detail is None:
        return None
    news = analytics.news(session, market, symbol, limit=8)
    data = {
        "symbol": detail["symbol"], "as_of": detail["as_of"], "scores": {k: {"score": v["score"], "activity": v["why"].get("activity")} for k, v in detail["scores"].items()},
        "top_buyers": detail["top_buyers"][:5], "top_sellers": detail["top_sellers"][:5], "signals": detail["signals"][:5],
        "recent_disclosures": [{"kap_id": e["source"]["id"], "date": e["effective_date"], "institution": e["institution"], "funds": e["funds"], "net_nominal": e["net_nominal"], "confidence": e["confidence"]} for e in detail["events"][:6]],
        "headlines": [{"id": n["id"], "source": n["source"], "title": n["title"], "url": n.get("url"), "published_at": n["published_at"], "ai": n.get("ai")} for n in news],
    }
    prompt = (f"Write a short institutional-flow assessment for {symbol.upper()} (3-5 sentences)." if lang == "en"
              else f"{symbol.upper()} için kısa kurumsal akış değerlendirmesi yaz (3-5 cümle).")
    return _write(session, "STOCK_ASSESSMENT", market, symbol.upper(), day, prompt, data, lang)


def daily_brief(session: Session, market: str, day: date | None = None, force: bool = False, lang: str = "tr") -> AiNote | None:
    day, lang = day or date.today(), norm_lang(lang)
    if not force and (c := _cached(session, "DAILY_BRIEF", market, "market", day, lang)):
        return c
    radar = analytics.radar(session, market, 8)
    flows7 = analytics.window_flows(session, market, 7)
    news = [n for n in analytics.news(session, market, limit=60) if n.get("tags") or (n.get("ai") or {}).get("relevance", 0) >= 60][:12]
    events = analytics.events(session, market, 10)
    data = {
        "market": market, "day": day.isoformat(),
        "window_default": {"days": radar.get("window_days"), "accumulated": radar["accumulated"][:6], "distributed": radar["distributed"][:6]},
        "last7d": {"accumulated": flows7["accumulated"][:6], "distributed": flows7["distributed"][:6]},
        "signals": radar["signals"][:8],
        "latest_disclosures": [{"kap_id": e["source"]["id"], "date": e["effective_date"], "symbol": e["symbol"], "institution": e["institution"], "net_nominal": e["net_nominal"], "confidence": e["confidence"]} for e in events],
        "headlines": [{"id": n["id"], "source": n["source"], "title": n["title"], "url": n.get("url"), "tags": n.get("tags"), "ai": n.get("ai")} for n in news],
    }
    if lang == "en":
        label = "Turkey (BIST / KAP)" if market == "TR" else "US (SEC 13F)"
        prompt = f"Write the morning brief for {label}: what funds did yesterday / in the latest period, active signals, related headlines, what to watch today. 5-8 sentences, plain text without headings."
    else:
        label = "Türkiye (BIST / KAP)" if market == "TR" else "ABD (SEC 13F)"
        prompt = f"{label} için sabah brifingi yaz: dün/son dönemde fonlar ne yaptı, aktif sinyaller, ilgili haberler, bugün izlenecekler. 5-8 cümle, başlıksız düz metin."
    return _write(session, "DAILY_BRIEF", market, "market", day, prompt, data, lang)


def _symbols_in(data: dict) -> list[str]:
    """Every ticker mentioned in the inputs, so the UI can link them in the prose."""
    out: set[str] = set()

    def walk(x):
        if isinstance(x, dict):
            for k, v in x.items():
                if k == "symbol" and isinstance(v, str):
                    out.add(v)
                else:
                    walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data)
    return sorted(out)


def _headlines_in(data: dict) -> list[dict]:
    return [{"id": h["id"], "title": h.get("title", ""), "source": h.get("source", ""), "url": h.get("url")} for h in data.get("headlines", []) if "id" in h]


def note_json(n: AiNote | None) -> dict | None:
    if n is None:
        return None
    inputs = n.data.get("inputs", {}) or {}
    return {"id": n.id, "kind": n.kind, "subject": n.subject, "as_of": n.as_of.isoformat(), "lang": n.lang, "content": n.content,
            "headline": n.data.get("headline", ""), "highlights": n.data.get("highlights", []), "watch": n.data.get("watch", []),
            "headline_ids": n.data.get("headline_ids", []), "confidence_note": n.data.get("confidence_note", ""),
            "symbols": _symbols_in(inputs), "headlines": _headlines_in(inputs), "kap_base": "https://www.kap.org.tr/tr/Bildirim/",
            "model": n.model, "created_at": n.created_at.isoformat()}
