"""Alert rules → notifications. Runs after every `compute_intelligence`; idempotent via dedup_key.

Rule types (params in AlertRule.params):
  NEW_FUND_POSITION  a fund opened a first position in the instrument         (instrument)
  FUND_EXIT          a fund fully exited the instrument                       (instrument)
  KAP_TRANSACTION    a new disclosed transaction touched the instrument/fund  (instrument | fund)
  SCORE_ABOVE        Smart Money Score crossed {threshold}                    (instrument)
  SIGNAL             one of {types} fired                                     (instrument)
  FUND_ACTIVITY      the fund had NEW/EXIT moves in the latest period         (fund)
Language is descriptive on purpose — never "buy"/"sell".
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instilens.domain.enums import ActivityType, ScoreType
from instilens.domain.models import (
    AlertRule,
    Fund,
    Instrument,
    Notification,
    PositionChange,
    Score,
    Signal,
    TransactionEvent,
    TransactionEventFund,
    User,
)
from instilens.services import live

RULE_TYPES = {"NEW_FUND_POSITION", "FUND_EXIT", "KAP_TRANSACTION", "SCORE_ABOVE", "SIGNAL", "FUND_ACTIVITY"}


WATCHLIST_STOCK_RULES = ("NEW_FUND_POSITION", "FUND_EXIT", "KAP_TRANSACTION", "SIGNAL")
WATCHLIST_FUND_RULES = ("FUND_ACTIVITY", "KAP_TRANSACTION")


def evaluate(session: Session, as_of: date) -> int:
    """Evaluate every active rule — explicit rules plus implicit ones for every watchlist item. Returns notifications created."""
    created = 0
    rules = list(session.scalars(select(AlertRule).where(AlertRule.is_active.is_(True))))
    rules += _watchlist_rules(session)
    langs = {str(u.id): (u.lang if u.lang in ("tr", "en") else "tr") for u in session.scalars(select(User))}
    for rule in rules:
        for key, title, body, link in _fire(session, rule, as_of, langs.get(str(rule.owner_id), "tr")):
            if _notify(session, rule, key, title, body, link):
                created += 1
    session.flush()
    return created


def _watchlist_rules(session: Session) -> list[AlertRule]:
    """Watching a stock or fund means: tell me when something happens to it. No rule form needed."""
    from instilens.domain.models import Watchlist, WatchlistItem

    out: list[AlertRule] = []
    for item, owner in session.execute(select(WatchlistItem, Watchlist.owner_id).join(Watchlist, Watchlist.id == WatchlistItem.watchlist_id)):
        kinds = WATCHLIST_STOCK_RULES if item.instrument_id else WATCHLIST_FUND_RULES if item.fund_id else ()
        for k in kinds:
            r = AlertRule(owner_id=owner, instrument_id=item.instrument_id, fund_id=item.fund_id, rule_type=k, params={}, is_active=True)
            r.id = -item.id  # transient; dedup keys become "wl:<item>:<kind>:…"
            out.append(r)
    return out


def _notify(session: Session, rule: AlertRule, key: str, title: str, body: str, link: str | None) -> bool:
    dedup = (f"wl:{-rule.id}:{rule.rule_type}:{key}" if (rule.id or 0) < 0 else f"{rule.id}:{key}")[:160]
    exists = session.scalar(select(Notification.id).where(Notification.owner_id == rule.owner_id, Notification.dedup_key == dedup))
    if exists:
        return False
    session.add(Notification(owner_id=rule.owner_id, alert_rule_id=rule.id if (rule.id or 0) > 0 else None, dedup_key=dedup, title=title, body=body, link=link))
    live.publish(session, "notification", owner_id=rule.owner_id, payload={"title": title, "link": link})
    return True


def _fire(session: Session, rule: AlertRule, as_of: date, lang: str = "tr"):
    inst = session.get(Instrument, rule.instrument_id) if rule.instrument_id else None
    fund = session.get(Fund, rule.fund_id) if rule.fund_id else None
    t = rule.rule_type

    if t in ("NEW_FUND_POSITION", "FUND_EXIT") and inst:
        want = ActivityType.NEW if t == "NEW_FUND_POSITION" else ActivityType.EXIT
        latest = session.scalar(select(func.max(PositionChange.period_end)).where(PositionChange.instrument_id == inst.id))
        if latest is None:
            return
        rows = session.execute(
            select(Fund.code).join(PositionChange, PositionChange.fund_id == Fund.id)
            .where(PositionChange.instrument_id == inst.id, PositionChange.period_end == latest, PositionChange.activity == want)
        ).scalars().all()
        if rows:
            if lang == "en":
                verb = "opened a new position" if want == ActivityType.NEW else "fully exited"
                title, tail = f"{inst.symbol}: {len(rows)} fund(s) {verb}", f" · period end {latest}"
            else:
                verb = "yeni pozisyon açtı" if want == ActivityType.NEW else "pozisyonunu tamamen kapattı"
                title, tail = f"{inst.symbol}: {len(rows)} fon {verb}", f" · dönem sonu {latest}"
            yield (f"{latest}", title, ", ".join(sorted(rows)) + tail, f"/stocks/{inst.symbol}")

    elif t == "KAP_TRANSACTION" and (inst or fund):
        stmt = select(TransactionEvent).where(TransactionEvent.is_superseded.is_(False))
        if inst:
            stmt = stmt.where(TransactionEvent.instrument_id == inst.id)
        if fund:
            stmt = stmt.where(TransactionEvent.funds.any(TransactionEventFund.fund_id == fund.id))
        for ev in session.scalars(stmt.order_by(TransactionEvent.published_at.desc()).limit(20)):
            sym = inst.symbol if inst else session.get(Instrument, ev.instrument_id).symbol
            side = ("position increase" if ev.net_nominal > 0 else "position decrease") if lang == "en" else ("pozisyon artışı" if ev.net_nominal > 0 else "pozisyon azalışı")
            yield (f"ev:{ev.id}", f"{sym}: KAP {side} ({ev.confidence})", f"{ev.net_nominal:+,} nominal · {ev.effective_date}", f"/stocks/{sym}")

    elif t == "SCORE_ABOVE" and inst:
        threshold = float(rule.params.get("threshold", 80))
        score = session.scalar(
            select(Score).where(Score.instrument_id == inst.id, Score.score_type == ScoreType.SMART_MONEY, Score.fund_id.is_(None))
            .order_by(Score.as_of.desc()).limit(1)
        )
        if score and float(score.adjusted_score) >= threshold:
            # once per calendar month while above the threshold (not every nightly compute)
            yield (f"{score.as_of:%Y-%m}:{int(threshold)}", f"{inst.symbol}: Smart Money Score {float(score.adjusted_score):.0f} ≥ {threshold:.0f}", f"{'computed' if lang == 'en' else 'hesaplama'} {score.as_of}", f"/stocks/{inst.symbol}")

    elif t == "SIGNAL" and inst:
        types = set(rule.params.get("types") or [])
        stmt = select(Signal).where(Signal.instrument_id == inst.id, Signal.window_end == as_of)
        for sig in session.scalars(stmt):
            if types and sig.signal_type not in types:
                continue
            # keyed by the signal episode (row id), not by the day, so an ongoing signal notifies once
            yield (f"{sig.signal_type}:{sig.id}", f"{inst.symbol}: {sig.signal_type.replace('_', ' ').title()} ({sig.strength})", f"{sig.window_start} → {sig.window_end} · {sig.confidence}", f"/stocks/{inst.symbol}")

    elif t == "FUND_ACTIVITY" and fund:
        latest = session.scalar(select(func.max(PositionChange.period_end)).where(PositionChange.fund_id == fund.id))
        if latest is None:
            return
        rows = session.execute(
            select(Instrument.symbol, PositionChange.activity).join(Instrument, Instrument.id == PositionChange.instrument_id)
            .where(PositionChange.fund_id == fund.id, PositionChange.period_end == latest, PositionChange.activity.in_([ActivityType.NEW, ActivityType.EXIT]))
        ).all()
        if rows:
            body = " · ".join(f"{s} {a}" for s, a in sorted(rows))
            yield (f"{latest}", f"{fund.code}: {len(rows)} {'new entries/exits' if lang == 'en' else 'yeni giriş/çıkış'}", body, f"/funds/{fund.code}")
