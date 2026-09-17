"""Alert rules → notifications. Runs after every `compute_intelligence`; idempotent via dedup_key.

Rule types (params in AlertRule.params):
  NEW_FUND_POSITION  a fund opened a first position in the instrument         (instrument)
  FUND_EXIT          a fund fully exited the instrument                       (instrument)
  KAP_TRANSACTION    a new disclosed transaction touched the instrument/fund  (instrument | fund)
  SCORE_ABOVE        Smart Money Score crossed {threshold}                    (instrument)
  SIGNAL             one of {types} fired                                     (instrument)
  FUND_ACTIVITY      the fund had NEW/EXIT moves in the latest period         (fund)
  INSIDER_BUY_CLUSTER  ≥3 insiders made open-market purchases in 30 days      (instrument; Form 4 on US, KAP on TR)
  PRICE_ABOVE / PRICE_BELOW  the daily close crossed {price}                (instrument, explicit rules only)
  PORTFOLIO_MOVE     funds moved on a symbol held in one of the owner's portfolios (implicit only, see below)
Language is descriptive on purpose — never "buy"/"sell".

PORTFOLIO_MOVE is never a stored rule: every (owner, held symbol) pair across the owner's portfolios
(services/portfolio.held_instruments) is one transient rule per evaluate, only while the owner's plan includes
portfolios (services/plans.allows). It fires once per (symbol, period_end) when the latest period's snapshot diffs
show a NEW or EXIT cluster (≥ PORTFOLIO_MOVE_MIN_FUNDS funds, the breadth the cluster signals need) or that many
funds increasing (NEW + ADD) or reducing (REDUCE + EXIT).

Price rules read `market_prices` closes — daily bars; the header feed carries the market strip, not stocks, so an
intraday crossing is seen at the next close. A rule fires on the close date that crossed the threshold (the previous
close was on the other side, or there is no previous close) and once per crossing: the dedup key is the close date.
Every consecutive pair of the last PRICE_LOOKBACK closes is checked, not only the newest one, so a crossing still
fires when two closes land between two evaluates. Closes before the one known when the rule was created
(`params.since`, set by `userdata.create_rule`) never count: a rule created while the close is already beyond its
threshold waits for the next crossing, unless that latest close is itself the crossing.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instilens.domain.enums import ActivityType, ScoreType, SignalType
from instilens.domain.models import (
    AlertRule,
    Fund,
    Instrument,
    MarketRow,
    Notification,
    PositionChange,
    Score,
    Signal,
    TransactionEvent,
    TransactionEventFund,
    User,
)
from instilens.services import live
from instilens.services.analytics import last_closes

PRICE_RULES = ("PRICE_ABOVE", "PRICE_BELOW")  # explicit rules with params {"price": number > 0, "since": date}; never implicit for a watched stock
RULE_TYPES = {"NEW_FUND_POSITION", "FUND_EXIT", "KAP_TRANSACTION", "SCORE_ABOVE", "SIGNAL", "FUND_ACTIVITY", "INSIDER_BUY_CLUSTER", *PRICE_RULES}
PORTFOLIO_MOVE = "PORTFOLIO_MOVE"  # implicit per held symbol; not in RULE_TYPES, so it cannot be created as an explicit rule
PORTFOLIO_MOVE_MIN_FUNDS = 3  # = engine.signals.CLUSTER_MIN_FUNDS
CURRENCY_SIGN = {"TRY": "₺", "USD": "$"}
PRICE_LOOKBACK = 10  # closes a price rule walks per evaluate (two trading weeks): a crossing survives that many missed runs


WATCHLIST_STOCK_RULES = ("NEW_FUND_POSITION", "FUND_EXIT", "KAP_TRANSACTION", "SIGNAL", "INSIDER_BUY_CLUSTER")  # both markets: Form 4 / KAP insider rows
WATCHLIST_FUND_RULES = ("FUND_ACTIVITY", "KAP_TRANSACTION")
INSIDER_SOURCE_LABEL = {"US": "Form 4", "TR": "KAP"}  # what the cluster notification cites as the reported source


def evaluate(session: Session, as_of: date) -> int:
    """Evaluate every active rule — explicit rules plus implicit ones for every watchlist item. Returns notifications created."""
    created = 0
    rules = list(session.scalars(select(AlertRule).where(AlertRule.is_active.is_(True))))
    rules += _watchlist_rules(session)
    rules += _portfolio_rules(session)
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


def _portfolio_rules(session: Session) -> list[AlertRule]:
    """Holding a symbol in a portfolio means: tell me when the funds move on it. One transient rule per (owner,
    symbol) whatever the number of portfolios it sits in; owners whose plan lacks portfolios get none."""
    from instilens.services import plans
    from instilens.services.portfolio import held_instruments

    out: list[AlertRule] = []
    allowed: dict[str, bool] = {}
    for owner, instrument_id in held_instruments(session):
        if owner not in allowed:
            allowed[owner] = plans.allows(session, owner, "portfolio")
        if not allowed[owner]:
            continue
        r = AlertRule(owner_id=owner, instrument_id=instrument_id, fund_id=None, rule_type=PORTFOLIO_MOVE, params={}, is_active=True)
        r.id = -instrument_id  # transient; dedup keys become "pf:<instrument>:PORTFOLIO_MOVE:<period_end>"
        out.append(r)
    return out


def _notify(session: Session, rule: AlertRule, key: str, title: str, body: str, link: str | None) -> bool:
    if (rule.id or 0) < 0:  # transient (watchlist item / portfolio holding): the prefix keeps the two id spaces apart
        dedup = f"{'pf' if rule.rule_type == PORTFOLIO_MOVE else 'wl'}:{-rule.id}:{rule.rule_type}:{key}"[:160]
    else:
        dedup = f"{rule.id}:{key}"[:160]
    exists = session.scalar(select(Notification.id).where(Notification.owner_id == rule.owner_id, Notification.dedup_key == dedup))
    if exists:
        return False
    session.add(Notification(owner_id=rule.owner_id, alert_rule_id=rule.id if (rule.id or 0) > 0 else None, dedup_key=dedup, title=title[:256], body=body, link=link))
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
            if (rule.id or 0) < 0 and sig.signal_type == SignalType.INSIDER_BUY_CLUSTER:
                continue  # a watched stock gets the dedicated INSIDER_BUY_CLUSTER rule below — one notification, not two
            # keyed by the signal episode (row id), not by the day, so an ongoing signal notifies once
            yield (f"{sig.signal_type}:{sig.id}", f"{inst.symbol}: {sig.signal_type.replace('_', ' ').title()} ({sig.strength})", f"{sig.window_start} → {sig.window_end} · {sig.confidence}", f"/stocks/{inst.symbol}")

    elif t == "INSIDER_BUY_CLUSTER" and inst:
        stmt = select(Signal).where(Signal.instrument_id == inst.id, Signal.signal_type == SignalType.INSIDER_BUY_CLUSTER, Signal.window_end == as_of)
        for sig in session.scalars(stmt):
            ev = sig.evidence or {}
            n, value, since = ev.get("insiders", 0), float(ev.get("value") or 0), ev.get("since", sig.window_start.isoformat())
            names = ", ".join(ev.get("names") or [])
            currency = session.get(MarketRow, inst.market_code).currency
            amount = f"{CURRENCY_SIGN.get(currency, currency)}{value:,.0f}"  # the priced purchases' total, in the market's currency
            source = INSIDER_SOURCE_LABEL.get(inst.market_code, inst.market_code)
            # A Form 4 code P is an open-market purchase; a KAP filing states no venue, so on BIST the title says
            # "bought shares (KAP)" — the same wording as the stock page's cluster chip.
            kap = inst.market_code == "TR"
            if lang == "en":
                title = f"{inst.symbol}: {n} insiders bought shares in the last 30 days ({source})" if kap else f"{inst.symbol}: {n} insiders bought on the open market in the last 30 days"
                body = f"{names} · {amount} reported ({source}) · since {since}"
            else:
                title = f"{inst.symbol}: {n} şirket içi kişi son 30 günde pay aldı ({source})" if kap else f"{inst.symbol}: {n} şirket içi kişi son 30 günde açık piyasadan hisse aldı"
                body = f"{names} · bildirilen tutar {amount} ({source}) · {since} tarihinden beri"
            # keyed by the signal episode (row id — compute_intelligence keeps the row across same-day recomputes and
            # extends it day by day): an ongoing cluster notifies once, however many days it lasts
            yield (f"cluster:{sig.id}", title, body, f"/stocks/{inst.symbol}")

    elif t in PRICE_RULES and inst:  # explicit rules only: no watchlist tuple carries a price rule
        price = rule.params.get("price")
        # One close more than the walk: the oldest row is only ever the "previous" of the next one — a close at the
        # edge of the window is not a crossing just because the window shows nothing before it.
        closes = list(reversed(last_closes(session, inst.id, as_of, PRICE_LOOKBACK + 1)))
        if price is None or not closes:
            return
        threshold = Decimal(str(price))
        since = date.fromisoformat(rule.params["since"]) if rule.params.get("since") else None
        above = t == "PRICE_ABOVE"
        beyond = (lambda c: c > threshold) if above else (lambda c: c < threshold)
        currency = session.get(MarketRow, inst.market_code).currency
        amount = lambda v: _price(v, lang, CURRENCY_SIGN.get(currency, currency))  # noqa: E731
        for i in range(1 if len(closes) > PRICE_LOOKBACK else 0, len(closes)):
            (on, close), previous = closes[i], (closes[i - 1][1] if i else None)
            if not beyond(close) or (previous is not None and beyond(previous)) or (since is not None and on < since):
                continue  # not beyond the threshold, already was at the previous close, or before the rule existed: no crossing on this date
            if lang == "en":
                title = f"{inst.symbol}: close {amount(close)} is {'above' if above else 'below'} the {amount(threshold)} threshold"
                body = f"close date {on} · previous close {amount(previous) if previous is not None else 'none'}"
            else:
                title = f"{inst.symbol}: kapanış {amount(close)} ile eşik {amount(threshold)} {'üzerinde' if above else 'altında'}"
                body = f"kapanış tarihi {on} · önceki kapanış {amount(previous) if previous is not None else 'yok'}"
            yield (f"{on}", title, body, f"/stocks/{inst.symbol}")

    elif t == PORTFOLIO_MOVE and inst:
        latest = session.scalar(select(func.max(PositionChange.period_end)).where(PositionChange.instrument_id == inst.id))
        if latest is None:
            return
        rows = session.execute(
            select(Fund.code, PositionChange.activity).join(PositionChange, PositionChange.fund_id == Fund.id)
            .where(PositionChange.instrument_id == inst.id, PositionChange.period_end == latest)
        ).all()
        by = {a: sorted(c for c, act in rows if act == a) for a in (ActivityType.NEW, ActivityType.ADD, ActivityType.REDUCE, ActivityType.EXIT)}
        new, exited = by[ActivityType.NEW], by[ActivityType.EXIT]
        inc, red = new + by[ActivityType.ADD], by[ActivityType.REDUCE] + exited
        if max(len(new), len(exited), len(inc), len(red)) < PORTFOLIO_MOVE_MIN_FUNDS:
            return
        codes = ", ".join(sorted(set(inc + red)))
        reduced = by[ActivityType.REDUCE]  # trimmed but still holding; the title keeps them apart from the funds that left
        if lang == "en":
            words = [(len(inc), "increased"), (len(reduced), "reduced"), (len(exited), "exited")]
            title = f"{inst.symbol} in your portfolio: {', '.join(f'{n} fund{'s' if n != 1 else ''} {w}' for n, w in words if n)} in the latest period"
            body = f"period end {latest} · new {len(new)} · increased {len(inc) - len(new)} · reduced {len(reduced)} · exited {len(exited)} · {codes}"
        else:
            words = [(len(inc), "artırdı"), (len(reduced), "azalttı"), (len(exited), "çıktı")]
            title = f"Portföyündeki {inst.symbol}: son dönemde {', '.join(f'{n} fon {w}' for n, w in words if n)}"
            body = f"dönem sonu {latest} · yeni {len(new)} · artıran {len(inc) - len(new)} · azaltan {len(reduced)} · çıkan {len(exited)} · {codes}"
        yield (f"{latest}", title, body, "/portfolio")

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


def _price(v: Decimal, lang: str, sign: str) -> str:
    """A close or threshold as the user reads it: trailing zeros dropped ("123.4 ₺", "120 ₺"), decimal comma in Turkish."""
    text = format(v.normalize(), "f")
    return f"{text.replace('.', ',') if lang == 'tr' else text} {sign}"
