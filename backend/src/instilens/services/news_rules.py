"""Rule evaluation for headlines (port of Newsomatic's keyword rules). Pure functions + a tiny CRUD."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.domain.models import NewsItem, NewsRule


def _terms(csv: str) -> list[list[str]]:
    """'banka, faiz+artış, halka arz' → [['banka'], ['faiz','artış'], ['halka arz']] (outer OR, inner AND)."""
    return [[t.strip().lower() for t in part.split("+") if t.strip()] for part in csv.split(",") if part.strip()]


def _has(text: str, term: str) -> bool:
    """Whole-word match; a trailing * allows suffixes (banka* → bankası, bankacılık)."""
    if term.endswith("*"):
        return re.search(rf"(?<!\w){re.escape(term[:-1])}", text) is not None
    return re.search(rf"(?<!\w){re.escape(term)}(?!\w)", text) is not None


def rule_matches(rule: NewsRule, title: str, source: str, published_at: datetime) -> bool:
    t = title.lower()
    if rule.max_age_days and published_at < datetime.now(UTC).replace(tzinfo=None) - timedelta(days=rule.max_age_days):
        return False
    if rule.only_sources and source.lower() not in {s.strip().lower() for s in rule.only_sources.split(",") if s.strip()}:
        return False
    if rule.remove_sources and source.lower() in {s.strip().lower() for s in rule.remove_sources.split(",") if s.strip()}:
        return False
    if any(_has(t, w) for group in _terms(rule.exclusion) for w in group):
        return False
    groups = _terms(rule.query)
    return not groups or any(all(_has(t, w) for w in group) for group in groups)


def apply_rules(session: Session, item: NewsItem, rules: list[NewsRule] | None = None, base_symbols: set[str] | None = None) -> None:
    """Recompute tags from scratch; symbols = base (keyword/AI matches) ∪ rule symbols."""
    rules = rules if rules is not None else active_rules(session, item.market_code)
    tags: set[str] = set()
    symbols = set(base_symbols if base_symbols is not None else (item.symbols or []))
    for r in rules:
        if rule_matches(r, item.title, item.source, item.published_at):
            tags.add(r.name)
            symbols.update(s.upper() for s in (r.symbols or []))
    item.tags, item.symbols = sorted(tags), sorted(symbols)


def active_rules(session: Session, market: str) -> list[NewsRule]:
    return session.scalars(select(NewsRule).where(NewsRule.market_code == market, NewsRule.is_active.is_(True))).all()


def reapply_all(session: Session, market: str, days: int = 7) -> int:
    from instilens.ingestion.news import _keywords, is_finance, match_symbols

    rules = active_rules(session, market)
    keywords = _keywords(session, market)
    n = 0
    for item in session.scalars(select(NewsItem).where(NewsItem.market_code == market, NewsItem.published_at >= datetime.now(UTC).replace(tzinfo=None) - timedelta(days=days))):
        if not is_finance(item.title, item.url, market):  # ticker is finance-only; drop noise that got in before the filter
            session.delete(item)
            continue
        base = set(match_symbols(item.title, keywords)) | {s.upper() for s in ((item.ai or {}).get("symbols") or [])}
        apply_rules(session, item, rules, base_symbols=base)
        n += 1
    session.flush()
    return n


DEFAULT_RULES: list[dict] = [
    {"name": "BIST & Fonlar", "market_code": "TR", "query": "borsa*, bist*, hisse*, fon, fonlar*, halka arz*, temettü*, bedelli, bedelsiz, spk, kap", "exclusion": "magazin, dizi, futbol, asya borsa*, avrupa borsa*"},
    {"name": "Makro TR", "market_code": "TR", "query": "faiz*, enflasyon*, tcmb, merkez bankası, kur, dolar*, bütçe*, cari açık", "exclusion": "çin, fed, ecb, avrupa merkez"},
    {"name": "Bankacılık", "market_code": "TR", "query": "banka*, bddk, kredi*, mevduat*", "exclusion": "çin, fed, ecb, avrupa merkez, dünya bankası, merkez bankası", "symbols": ["GARAN", "AKBNK", "YKBNK", "ISCTR", "HALKB", "VAKBN"]},
    {"name": "Savunma", "market_code": "TR", "query": "savunma, aselsan, ihracat+savunma, ssb", "exclusion": "", "symbols": ["ASELS"]},
    {"name": "Wall Street", "market_code": "US", "query": "stocks, wall street, s&p, nasdaq, fed, earnings, treasury", "exclusion": "", "language": "en"},
    {"name": "Big Tech", "market_code": "US", "query": "apple, microsoft, nvidia, alphabet, google, amazon, meta, tesla", "exclusion": "", "language": "en", "symbols": ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA"]},
]


def seed_default_rules(session: Session) -> int:
    existing = {r.name for r in session.scalars(select(NewsRule))}
    n = 0
    for d in DEFAULT_RULES:
        if d["name"] not in existing:
            session.add(NewsRule(**d))
            n += 1
    session.flush()
    return n
