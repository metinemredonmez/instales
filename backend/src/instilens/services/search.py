"""Full-text search over `searchable_texts` (built by services/search_index).

Two engines, one output shape (the /search/text contract):
  Postgres  websearch_to_tsquery('simple', q) against the stored tsvector — "quoted phrases", -excluded words and OR
            work as in a web search box; words match as typed (no stemming, so Turkish suffixes are literal);
            ts_rank_cd orders, ts_headline cuts the snippet. The stored vector weighs the title A and the body D
            (migration f2a3b4c5d6e7), so with ts_rank_cd's default weights a title hit counts ten times a body hit
            and the document *about* the query comes before the report that mentions it once. This is the branch
            production runs; it cannot run on the SQLite test database (tests/test_search.py covers everything
            after the query — kinds, limit, the hit shape, the snippet marks and fallbacks — through the LIKE
            engine), so it is exercised in production and was checked by hand against a local Postgres 14 when
            written.
  SQLite    every token must appear in the title or the body and no -excluded one may (LIKE, wildcards escaped as
            analytics.search does); a Python ranker (title hits count three times a body hit — the same order as
            the weighted vector, not the same numbers) and a Python snippet builder give the same fields. Dev and
            tests only.
Both engines put superseded disclosures (`SearchableText.superseded`: a notice or filing a later correction replaced)
after every live hit, whatever their score, and every hit carries the flag. A query without a positive term
("-foo") matches nothing on either engine. Snippets are plain text of at most SNIPPET_MAX characters with each
matched term wrapped in «»; the UI does the highlighting. `total` is the match count before the limit, capped at
TOTAL_CAP.
"""

from __future__ import annotations

import re

from sqlalchemy import func, literal_column, or_, select
from sqlalchemy.orm import Session

from instilens.domain.models import SearchableText
from instilens.services.search_index import KINDS

SNIPPET_MAX = 240
TOTAL_CAP = 500
Q_MIN, Q_MAX = 2, 128
MARK_OPEN, MARK_CLOSE = "«", "»"
_HEADLINE_OPTS = f"StartSel={MARK_OPEN}, StopSel={MARK_CLOSE}, MaxWords=30, MinWords=12, MaxFragments=2"
_TOKEN_SPLIT = re.compile(r"\s+")


def parse_kinds(value: str | list[str] | None) -> list[str]:
    """`kinds` as the route receives it (comma-separated) or the tool passes it (a list); every kind is known or
    ValueError names the bad one. Empty means every kind."""
    if not value:
        return list(KINDS)
    parts = value.split(",") if isinstance(value, str) else list(value)
    kinds = [p.strip().lower() for p in parts if p.strip()]
    for k in kinds:
        if k not in KINDS:
            raise ValueError(f"unknown kind {k!r}; expected one of {', '.join(KINDS)}")
    return list(dict.fromkeys(kinds)) or list(KINDS)


def tokens(q: str, *, excluded: bool = False) -> list[str]:
    """The words of a query for the LIKE engine and the snippet builder: quotes and a leading + are dropped (the
    SQLite fallback has no phrase syntax), OR is not a word, a word with a leading - is an exclusion — left out by
    default, the only ones returned with `excluded`. Order kept, duplicates removed."""
    out: list[str] = []
    for raw in _TOKEN_SPLIT.split(q.strip()):
        word = raw.lstrip("-+").strip("\"'“”‘’")
        if word and word.upper() != "OR" and raw.startswith("-") == excluded and word not in out:
            out.append(word)
    return out


def text_search(session: Session, market: str, q: str, kinds: list[str] | None = None, limit: int = 20) -> dict:
    """The /search/text payload: {q, market, total, hits}; hits ranked by relevance, then newest first."""
    q = q.strip()[:Q_MAX]
    wanted = parse_kinds(kinds)
    limit = max(1, min(int(limit), 50))
    if len(q) < Q_MIN or not tokens(q):
        # Nothing, or only exclusions: websearch_to_tsquery would turn "-foo" into !'foo' and match every other row.
        return {"q": q, "market": market, "total": 0, "hits": []}
    if session.get_bind().dialect.name == "postgresql":
        hits, total = _postgres(session, market, q, wanted, limit)
    else:
        hits, total = _like(session, market, q, wanted, limit)
    return {"q": q, "market": market, "total": total, "hits": hits}


def _hit(row: SearchableText, snippet: str, score: float) -> dict:
    return {"kind": row.kind, "id": row.ref_id, "title": row.title, "snippet": snippet, "date": row.date.isoformat(), "symbols": list(row.symbols or []),
            "source": row.source, "url": row.url, "link": row.link, "superseded": bool(row.superseded), "score": round(float(score), 4)}


# --------------------------------------------------------------------------- Postgres


def _postgres(session: Session, market: str, q: str, kinds: list[str], limit: int) -> tuple[list[dict], int]:
    """websearch_to_tsquery over the generated `tsv` column (migration f2a3b4c5d6e7; not mapped by the ORM, hence the
    literal column). The headline is computed for the `limit` rows only — ts_headline re-parses the body."""
    tsv = literal_column("searchable_texts.tsv")
    config = literal_column("'simple'")  # an untyped literal resolves to regconfig; a bound text parameter would not
    query = func.websearch_to_tsquery(config, q)
    matched = select(SearchableText.id).where(SearchableText.market_code == market, SearchableText.kind.in_(kinds), tsv.op("@@")(query))
    total = session.scalar(select(func.count()).select_from(matched.limit(TOTAL_CAP).subquery())) or 0
    if not total:
        return [], 0
    rank = func.ts_rank_cd(tsv, query).label("score")
    order = (SearchableText.superseded.asc(), rank.desc(), SearchableText.date.desc(), SearchableText.id.desc())
    top = matched.add_columns(rank).order_by(*order).limit(limit).subquery()
    headline = func.ts_headline(config, SearchableText.body, query, _HEADLINE_OPTS)
    stmt = (
        select(SearchableText, top.c.score, headline)
        .join(top, top.c.id == SearchableText.id)
        .order_by(SearchableText.superseded.asc(), top.c.score.desc(), SearchableText.date.desc(), SearchableText.id.desc())
    )
    words = tokens(q)
    hits = []
    for row, score, text in session.execute(stmt):
        snippet = _trim(text.replace(" ... ", " … ")) if text else ""
        if MARK_OPEN not in snippet:
            # ts_headline marks nothing when the body holds no passage with every query word (the match sits in the
            # title, or the words are spread over title and body): fall back to the Python builder, body first.
            snippet = _fallback(row, words) or snippet or _trim(row.body)
        hits.append(_hit(row, snippet, score))
    return hits, int(total)


def _fallback(row: SearchableText, words: list[str]) -> str:
    """A marked window of the body, else of the title, else nothing."""
    for text in (row.body, row.title):
        if _find(text, words):
            return snippet_for(text, words)
    return ""


# --------------------------------------------------------------------------- SQLite


def _like(session: Session, market: str, q: str, kinds: list[str], limit: int) -> tuple[list[dict], int]:
    words = tokens(q)
    if not words:
        return [], 0
    stmt = select(SearchableText).where(SearchableText.market_code == market, SearchableText.kind.in_(kinds))
    for word in words:
        stmt = stmt.where(_contains(word))
    for word in tokens(q, excluded=True):
        stmt = stmt.where(~_contains(word))
    rows = session.scalars(stmt.order_by(SearchableText.date.desc(), SearchableText.id.desc()).limit(TOTAL_CAP)).all()
    ranked = sorted(((_score(r, words), r) for r in rows), key=lambda t: (bool(t[1].superseded), -t[0], _key(t[1])))
    hits = [_hit(row, _fallback(row, words) or _trim(row.body), score) for score, row in ranked[:limit]]
    return hits, len(rows)


def _contains(word: str):
    pattern = "%" + word.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"  # user text must not act as LIKE wildcards
    return or_(SearchableText.title.ilike(pattern, escape="\\"), SearchableText.body.ilike(pattern, escape="\\"))


def _key(row: SearchableText) -> tuple[int, int]:
    return (-row.date.toordinal(), -row.id)  # newest first, then the newest row


def _score(row: SearchableText, words: list[str]) -> float:
    """Title hits weigh three body hits; body hits saturate at ten per word so a long 13F does not outrank a
    headline that is about the query."""
    score = 0
    for word in words:
        pattern = _pattern([word])
        score += 3 * len(pattern.findall(row.title)) + min(10, len(pattern.findall(row.body)))
    return score


# --------------------------------------------------------------------------- snippets (shared)


def _pattern(words: list[str]) -> re.Pattern:
    return re.compile("|".join(re.escape(w) for w in sorted(words, key=len, reverse=True)), re.IGNORECASE)


def _find(text: str, words: list[str]) -> re.Match | None:
    return _pattern(words).search(text) if words and text else None


def _trim(text: str, limit: int = SNIPPET_MAX) -> str:
    """At most `limit` characters, cut at a word, an unclosed « closed so the UI never sees a dangling mark."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    cut = text[: limit - 1]
    if " " in cut[limit // 2:]:
        cut = cut[: cut.rfind(" ")]
    cut = cut.rstrip()
    if cut.count(MARK_OPEN) > cut.count(MARK_CLOSE):
        cut = cut[: limit - 2].rstrip() + MARK_CLOSE  # room for the closing mark and the ellipsis
    return cut + "…"


def snippet_for(text: str, words: list[str], limit: int = SNIPPET_MAX) -> str:
    """A window of `text` around the first matched word with every match wrapped in «»; the head of the text when
    nothing matches. Never longer than `limit` characters, marks and ellipses included."""
    text = " ".join((text or "").split())
    pattern = _pattern(words) if words else None
    first = pattern.search(text) if pattern else None
    if first is None:
        return _trim(text, limit)
    # Shrink the raw window until the marked result fits: every match adds two characters.
    width = limit - 2
    while True:
        start = max(0, first.start() - width // 3)
        if start:
            boundary = text.rfind(" ", max(0, start - 30), start)  # begin at a word, at most 30 characters earlier
            start = boundary + 1 if boundary >= 0 else start
        end = min(len(text), start + width)
        if end < len(text) and " " in text[max(first.end(), start + width // 2):end]:
            end = text.rfind(" ", max(first.end(), start + width // 2), end)  # end at a word, never before the match
        window = text[start:end].strip()
        marked = pattern.sub(lambda m: f"{MARK_OPEN}{m.group(0)}{MARK_CLOSE}", window)
        out = ("…" if start > 0 else "") + marked + ("…" if end < len(text) else "")
        if len(out) <= limit or width <= 40:
            return out if len(out) <= limit else _trim(out, limit)
        width -= max(8, len(out) - limit)
