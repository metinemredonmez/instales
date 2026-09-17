"""Watchlists, alert rules, notifications — read models and mutations for one owner."""

from __future__ import annotations

import math
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instilens.domain.enums import ScoreType
from instilens.domain.models import (
    AlertRule,
    Fund,
    Instrument,
    Notification,
    Score,
    Watchlist,
    WatchlistItem,
)
from instilens.services import plans
from instilens.services.alerts import PRICE_RULES, RULE_TYPES
from instilens.services.analytics import last_closes

MAX_PRICE = 1e9  # a price threshold above this is not a price; it would also render as hundreds of digits in a notification title


class UserDataError(Exception):
    pass


def _default_watchlist(session: Session, owner: str) -> Watchlist:
    wl = session.scalar(select(Watchlist).where(Watchlist.owner_id == owner).order_by(Watchlist.id).limit(1))
    if wl is None:
        wl = Watchlist(owner_id=owner, name="Takip listem")
        session.add(wl)
        session.flush()
    return wl


def resolve_subject(session: Session, market: str, symbol: str | None, fund_code: str | None) -> tuple[Instrument | None, Fund | None]:
    inst = fund = None
    if symbol:
        inst = session.scalar(select(Instrument).where(Instrument.market_code == market, Instrument.symbol == symbol.upper()))
        if inst is None:
            raise UserDataError(f"unknown symbol {symbol}")
    if fund_code:
        fund = session.scalar(select(Fund).where(Fund.code == fund_code.upper()))
        if fund is None:
            raise UserDataError(f"unknown fund {fund_code}")
    if inst is None and fund is None:
        raise UserDataError("symbol or fund_code is required")
    return inst, fund


def list_watchlist(session: Session, owner: str) -> list[dict]:
    wl = _default_watchlist(session, owner)
    out = []
    for item in session.scalars(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id).order_by(WatchlistItem.id)):
        if item.instrument_id:
            inst = session.get(Instrument, item.instrument_id)
            score = session.scalar(
                select(Score).where(Score.instrument_id == inst.id, Score.score_type == ScoreType.SMART_MONEY, Score.fund_id.is_(None)).order_by(Score.as_of.desc()).limit(1)
            )
            act = (score.components.get("activity", {}) if score else {})
            out.append({"id": item.id, "kind": "stock", "ref": inst.symbol, "name": inst.name, "market": inst.market_code, "smart_money_score": float(score.adjusted_score) if score else None,
                        "funds_increasing": act.get("funds_increasing"), "funds_reducing": act.get("funds_reducing"), "net_flow_value": float(act.get("net_flow_value", 0) or 0)})
        elif item.fund_id:
            fund = session.get(Fund, item.fund_id)
            out.append({"id": item.id, "kind": "fund", "ref": fund.code, "name": fund.name, "institution": fund.institution.name})
    return out


def add_watchlist_item(session: Session, owner: str, market: str, symbol: str | None, fund_code: str | None) -> dict:
    inst, fund = resolve_subject(session, market, symbol, fund_code)
    wl = _default_watchlist(session, owner)
    dup = session.scalar(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id, WatchlistItem.instrument_id == (inst.id if inst else None), WatchlistItem.fund_id == (fund.id if fund else None)))
    if dup:
        return {"id": dup.id, "created": False}
    # The plan's per-watchlist cap (services/plans; a no-op unless plans are enforced). Rows already above it stay.
    plans.enforce_limit(session, owner, "watchlist_items", session.scalar(select(func.count(WatchlistItem.id)).where(WatchlistItem.watchlist_id == wl.id)) or 0)
    item = WatchlistItem(watchlist_id=wl.id, instrument_id=inst.id if inst else None, fund_id=fund.id if fund else None)
    session.add(item)
    session.flush()
    return {"id": item.id, "created": True}


def remove_watchlist_item(session: Session, owner: str, item_id: int) -> bool:
    wl = _default_watchlist(session, owner)
    item = session.scalar(select(WatchlistItem).where(WatchlistItem.id == item_id, WatchlistItem.watchlist_id == wl.id))
    if item is None:
        return False
    session.delete(item)
    return True


def list_rules(session: Session, owner: str) -> list[dict]:
    """Every rule of the owner across markets, newest first; `market` is the subject's (a price threshold reads in
    that market's currency, whichever market the page is on)."""
    out = []
    for r in session.scalars(select(AlertRule).where(AlertRule.owner_id == owner).order_by(AlertRule.id.desc())):
        inst = session.get(Instrument, r.instrument_id) if r.instrument_id else None
        fund = session.get(Fund, r.fund_id) if r.fund_id else None
        out.append({
            "id": r.id, "rule_type": r.rule_type, "params": r.params, "is_active": r.is_active,
            "symbol": inst.symbol if inst else None,
            "fund_code": fund.code if fund else None,
            "market": inst.market_code if inst else fund.institution.market_code if fund else None,
        })
    return out


def create_rule(session: Session, owner: str, market: str, rule_type: str, symbol: str | None, fund_code: str | None, params: dict) -> dict:
    if rule_type not in RULE_TYPES:
        raise UserDataError(f"unknown rule_type {rule_type}")
    inst, fund = resolve_subject(session, market, symbol, fund_code)
    if rule_type == "INSIDER_BUY_CLUSTER" and inst is None:
        raise UserDataError("INSIDER_BUY_CLUSTER needs a symbol (insider filings — Form 4, KAP — describe stocks, not funds)")
    if rule_type in PRICE_RULES:
        if inst is None:
            raise UserDataError(f"{rule_type} needs a symbol (daily closes exist for stocks only)")
        price = _positive_number((params or {}).get("price"))
        if price is None:
            raise UserDataError(f"params.price must be a number above 0 and at most {MAX_PRICE:.0f}")
        # `since`: the latest close known today — closes before it never fire this rule (alerts.PRICE_RULES); a
        # stock without a close yet starts today, so a later history backfill cannot fire it for past crossings.
        closes = last_closes(session, inst.id, date.today(), 1)
        params = {**(params or {}), "price": price, "since": (closes[0][0] if closes else date.today()).isoformat()}
    # The plan's cap on active rules (soft-deleted ones do not count); a no-op unless plans are enforced.
    plans.enforce_limit(session, owner, "alert_rules", session.scalar(select(func.count(AlertRule.id)).where(AlertRule.owner_id == owner, AlertRule.is_active.is_(True))) or 0)
    rule = AlertRule(owner_id=owner, instrument_id=inst.id if inst else None, fund_id=fund.id if fund else None, rule_type=rule_type, params=params or {})
    session.add(rule)
    session.flush()
    return {"id": rule.id}


def _positive_number(value) -> float | None:
    """A finite number above zero and at most MAX_PRICE, as the JSON body carries it (a numeric string is accepted
    too), rounded to six decimals — finer than any tick size, and a threshold like 1e-300 rounds to 0 and is refused
    instead of printing 300 digits; None otherwise."""
    if isinstance(value, bool):
        return None
    try:
        number = round(float(Decimal(str(value).strip())) if isinstance(value, str) else float(value), 6)
    except (TypeError, ValueError, ArithmeticError):
        return None
    return number if math.isfinite(number) and 0 < number <= MAX_PRICE else None


def delete_rule(session: Session, owner: str, rule_id: int) -> bool:
    rule = session.scalar(select(AlertRule).where(AlertRule.id == rule_id, AlertRule.owner_id == owner))
    if rule is None:
        return False
    rule.is_active = False  # keep history for notifications; soft delete
    return True


def list_notifications(session: Session, owner: str, limit: int = 50) -> list[dict]:
    rows = session.scalars(select(Notification).where(Notification.owner_id == owner).order_by(Notification.id.desc()).limit(limit))
    return [{"id": n.id, "title": n.title, "body": n.body, "link": n.link, "created_at": n.created_at.isoformat(), "read_at": n.read_at.isoformat() if n.read_at else None} for n in rows]


def mark_read(session: Session, owner: str, notification_id: int | None) -> int:
    stmt = select(Notification).where(Notification.owner_id == owner, Notification.read_at.is_(None))
    if notification_id is not None:
        stmt = stmt.where(Notification.id == notification_id)
    n = 0
    for row in session.scalars(stmt):
        row.read_at = datetime.now(UTC)
        n += 1
    return n
