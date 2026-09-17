"""Watchlists, alert rules, notifications — read models and mutations for one owner."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
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
from instilens.services.alerts import RULE_TYPES


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
            out.append({"id": item.id, "kind": "stock", "ref": inst.symbol, "name": inst.name, "smart_money_score": float(score.adjusted_score) if score else None,
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
    out = []
    for r in session.scalars(select(AlertRule).where(AlertRule.owner_id == owner).order_by(AlertRule.id.desc())):
        out.append({
            "id": r.id, "rule_type": r.rule_type, "params": r.params, "is_active": r.is_active,
            "symbol": session.get(Instrument, r.instrument_id).symbol if r.instrument_id else None,
            "fund_code": session.get(Fund, r.fund_id).code if r.fund_id else None,
        })
    return out


def create_rule(session: Session, owner: str, market: str, rule_type: str, symbol: str | None, fund_code: str | None, params: dict) -> dict:
    if rule_type not in RULE_TYPES:
        raise UserDataError(f"unknown rule_type {rule_type}")
    inst, fund = resolve_subject(session, market, symbol, fund_code)
    if rule_type == "INSIDER_BUY_CLUSTER" and (inst is None or inst.market_code != "US"):
        raise UserDataError("INSIDER_BUY_CLUSTER needs a US symbol (SEC Form 4 data exists for US issuers only)")
    rule = AlertRule(owner_id=owner, instrument_id=inst.id if inst else None, fund_id=fund.id if fund else None, rule_type=rule_type, params=params or {})
    session.add(rule)
    session.flush()
    return {"id": rule.id}


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
