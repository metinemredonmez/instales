"""Headline aggregation from RSS feeds (and NewsAPI when a key is set). Headlines + links only.

Symbol matching: a headline mentions an instrument when it contains the ticker as a word or the
first distinctive word of the company name (≥ 5 letters). Good enough for "related news" and for
the news × flow signal; false positives are cheap because nothing is computed from them.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime

import feedparser
import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.domain.models import Instrument, NewsItem
from instilens.services.news_rules import active_rules, apply_rules

log = logging.getLogger(__name__)

DEFAULT_FEEDS: dict[str, list[tuple[str, str]]] = {
    "TR": [
        ("AA Ekonomi", "https://www.aa.com.tr/tr/rss/default?cat=ekonomi"),
        ("Bloomberg HT", "https://www.bloomberght.com/rss"),
        ("Dünya", "https://www.dunya.com/rss"),
        ("Ekonomim", "https://www.ekonomim.com/rss"),
        ("BigPara", "https://bigpara.hurriyet.com.tr/rss/"),
    ],
    "US": [
        ("CNBC", "https://www.cnbc.com/id/100003114/device/rss/rss.html"),
        ("MarketWatch", "https://feeds.content.dowjones.io/public/rss/mw_topstories"),
        ("Yahoo Finance", "https://finance.yahoo.com/news/rssindex"),
    ],
}
GENERIC = {"HOLDING", "SANAYI", "SANAYİ", "TICARET", "TİCARET", "ENERJI", "ENERJİ", "YATIRIM", "BANKASI", "TURK", "TÜRK", "TURKIYE", "TÜRKİYE", "GRUP", "GROUP", "INC", "CORP", "CO", "LTD", "PLC", "TRUST", "FUND", "ETF", "PORTFOY", "PORTFÖY"}


def _keywords(session: Session, market: str) -> dict[str, list[re.Pattern]]:
    out: dict[str, list[re.Pattern]] = {}
    for inst in session.scalars(select(Instrument).where(Instrument.market_code == market)):
        if not inst.symbol.isalpha() or len(inst.symbol) < 3:
            continue  # CUSIP placeholders etc.
        pats = [re.compile(rf"\b{re.escape(inst.symbol)}\b")]
        first = (inst.name or "").replace(".", " ").split()
        if first and len(first[0]) >= 5 and first[0].upper() not in GENERIC and first[0].upper() != inst.symbol:
            pats.append(re.compile(rf"\b{re.escape(first[0])}", flags=re.I))
        out[inst.symbol] = pats
    return out


def match_symbols(title: str, keywords: dict[str, list[re.Pattern]]) -> list[str]:
    upper = title.upper()
    return sorted(sym for sym, pats in keywords.items() if any(p.search(upper if p.flags & re.I == 0 else title) for p in pats))[:5]


def fetch_feeds(session: Session, market: str, feeds: list[tuple[str, str]] | None = None, newsapi_key: str | None = None, max_age_days: int = 7) -> int:
    keywords = _keywords(session, market)
    known = set(session.scalars(select(NewsItem.url)))
    added = 0
    entries: list[tuple[str, str, str, datetime]] = []
    for source, url in feeds or DEFAULT_FEEDS.get(market, []):
        try:
            raw = httpx.get(url, timeout=20, follow_redirects=True, headers={"User-Agent": "InstiLens/0.1 (+news ticker)"}).text
            parsed = feedparser.parse(raw)
        except Exception as exc:  # a dead feed must not break the run
            log.warning("feed %s failed: %s", source, exc)
            continue
        for e in parsed.entries[:60]:
            link, title = (e.get("link") or "").strip(), (e.get("title") or "").strip()
            if not link or not title:
                continue
            ts = e.get("published_parsed") or e.get("updated_parsed")
            published = datetime.fromtimestamp(time.mktime(ts), tz=UTC).replace(tzinfo=None) if ts else datetime.now(UTC).replace(tzinfo=None)
            entries.append((source, title, link, published))
    rules = active_rules(session, market)
    if newsapi_key:
        entries += _newsapi(market, newsapi_key)
        for r in rules:  # rule-specific NewsAPI queries (Newsomatic's query_string)
            if r.newsapi_query:
                entries += _newsapi(market, newsapi_key, query=r.newsapi_query, language=r.language or None)
    cutoff = datetime.now(UTC).replace(tzinfo=None).timestamp() - max_age_days * 86400
    for source, title, link, published in entries:
        if link in known or published.timestamp() < cutoff:
            continue
        item = NewsItem(market_code=market, source=source, title=title[:512], url=link[:1024], published_at=published, symbols=match_symbols(title, keywords), tags=[])
        apply_rules(session, item, rules)
        session.add(item)
        known.add(link)
        added += 1
    session.flush()
    return added


def _newsapi(market: str, key: str, query: str | None = None, language: str | None = None) -> list[tuple[str, str, str, datetime]]:
    """Optional NewsAPI.org source (the same API Newsomatic relies on). Free tier is delayed/non-commercial."""
    q = {"TR": {"q": "borsa OR BIST OR hisse", "language": "tr"}, "US": {"q": "stocks OR Wall Street", "language": "en"}}[market]
    if query:
        q = {"q": query, "language": language or q["language"]}
    try:
        r = httpx.get("https://newsapi.org/v2/everything", params={**q, "sortBy": "publishedAt", "pageSize": 50}, headers={"X-Api-Key": key}, timeout=20)
        r.raise_for_status()
    except Exception as exc:
        log.warning("newsapi failed: %s", exc)
        return []
    out = []
    for a in r.json().get("articles", []):
        try:
            published = datetime.fromisoformat(a["publishedAt"].replace("Z", "+00:00")).astimezone(UTC).replace(tzinfo=None)
        except Exception:
            published = datetime.now(UTC).replace(tzinfo=None)
        out.append(((a.get("source") or {}).get("name") or "NewsAPI", a.get("title") or "", a.get("url") or "", published))
    return out
