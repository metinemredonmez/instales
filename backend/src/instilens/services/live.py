"""Live events: the one-way "something changed" channel behind /events/stream.

Producers call publish() inside the transaction that made the change, so a tab never refetches before the rows
are visible. Consumers tail the table by id (see api/routes/v1.stream_events); rows older than a day are swept.
Deliberately no broker and no LISTEN/NOTIFY: a few users × one indexed query every few seconds is the whole cost.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from instilens.domain.models import LiveEvent

# kind → what the SPA refetches
#   notification  the bell (private to owner_id)
#   compute       scores/signals/positions changed → radar, stock, screener, watchlist, freshness
#   news          new headlines for a market
#   brief         morning brief (re)generated for a market
#   pipeline      admin pipeline started/finished
KINDS = ("notification", "compute", "news", "brief", "pipeline")
KEEP = timedelta(hours=24)


def publish(session: Session, kind: str, *, market: str | None = None, owner_id: str | None = None, payload: dict | None = None) -> LiveEvent:
    if kind not in KINDS:
        raise ValueError(f"unknown live event kind {kind!r}")
    ev = LiveEvent(kind=kind, market_code=market, owner_id=owner_id, payload=payload or {})
    session.add(ev)
    session.flush()
    return ev


def latest_id(session: Session) -> int:
    return session.scalar(select(func.max(LiveEvent.id))) or 0


def since(session: Session, after_id: int, *, market: str, owner_id: str, limit: int = 200) -> list[LiveEvent]:
    """Events newer than `after_id` that this tab cares about: global ones, this market's, and this user's private ones."""
    stmt = (
        select(LiveEvent)
        .where(LiveEvent.id > after_id)
        .where(or_(LiveEvent.market_code.is_(None), LiveEvent.market_code == market))
        .where(or_(LiveEvent.owner_id.is_(None), LiveEvent.owner_id == owner_id))
        .order_by(LiveEvent.id)
        .limit(limit)
    )
    return list(session.scalars(stmt))


def prune(session: Session, keep: timedelta = KEEP) -> int:
    cutoff = datetime.now(UTC).replace(tzinfo=None) - keep
    # Nobody holds swept rows; skip the in-session evaluation (it would compare aware defaults with the naive cutoff).
    return session.execute(delete(LiveEvent).where(LiveEvent.created_at < cutoff).execution_options(synchronize_session=False)).rowcount or 0
