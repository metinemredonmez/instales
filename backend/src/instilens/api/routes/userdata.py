from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from instilens.api.deps import current_user, get_session
from instilens.domain.models import User
from instilens.services import userdata
from instilens.services.alerts import RULE_TYPES

router = APIRouter(prefix="/api/v1", tags=["user"], dependencies=[Depends(current_user)])
MarketParam = Query("TR", pattern="^(TR|US)$")


class SubjectBody(BaseModel):
    symbol: str | None = Field(None, max_length=16)
    fund_code: str | None = Field(None, max_length=16)
    market: str = Field("TR", pattern="^(TR|US)$")


class RuleBody(SubjectBody):
    rule_type: str
    params: dict = Field(default_factory=dict)


def _owner(user: User) -> str:
    return str(user.id)


@router.get("/watchlist")
def get_watchlist(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return userdata.list_watchlist(session, _owner(user))


@router.post("/watchlist", status_code=201)
def post_watchlist(body: SubjectBody, user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        return userdata.add_watchlist_item(session, _owner(user), body.market, body.symbol, body.fund_code)
    except userdata.UserDataError as exc:
        raise HTTPException(404, str(exc)) from exc


@router.delete("/watchlist/{item_id}", status_code=204)
def delete_watchlist(item_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    if not userdata.remove_watchlist_item(session, _owner(user), item_id):
        raise HTTPException(404, "not found")


@router.get("/alerts/rules")
def get_rules(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return {"rule_types": sorted(RULE_TYPES), "rules": userdata.list_rules(session, _owner(user))}


@router.post("/alerts/rules", status_code=201)
def post_rule(body: RuleBody, user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        return userdata.create_rule(session, _owner(user), body.market, body.rule_type, body.symbol, body.fund_code, body.params)
    except userdata.UserDataError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.delete("/alerts/rules/{rule_id}", status_code=204)
def delete_rule(rule_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    if not userdata.delete_rule(session, _owner(user), rule_id):
        raise HTTPException(404, "not found")


@router.get("/alerts/notifications")
def get_notifications(limit: int = 50, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return userdata.list_notifications(session, _owner(user), limit)


@router.post("/alerts/notifications/read")
def read_notifications(id: int | None = None, user: User = Depends(current_user), session: Session = Depends(get_session)):
    return {"marked": userdata.mark_read(session, _owner(user), id)}


@router.post("/alerts/evaluate")
def evaluate_now(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Manual trigger (dev/ops). Production runs this after every compute."""
    from datetime import date

    from instilens.services.alerts import evaluate
    from instilens.services.analytics import latest_score_date

    return {"created": evaluate(session, latest_score_date(session) or date.today())}


class NotifySettings(BaseModel):
    """Partial update: only the fields present in the body change."""

    notify_email: bool | None = None
    notify_telegram_chat_id: str | None = Field(None, max_length=32)
    notify_brief: bool | None = None
    brief_markets: list[str] | None = Field(None, max_length=2)  # which morning briefs to receive: subset of TR / US
    lang: str | None = Field(None, pattern="^(tr|en)$")


@router.get("/me/settings")
def get_settings(user: User = Depends(current_user)):
    from instilens.services.notify import user_brief_markets

    return {"email": user.email, "notify_email": user.notify_email, "notify_telegram_chat_id": user.notify_telegram_chat_id, "notify_brief": user.notify_brief,
            "brief_markets": user_brief_markets(user), "lang": user.lang, "email_verified": bool(user.email_verified),
            "channels": {"telegram": bool(__import__("instilens.config", fromlist=["settings"]).settings.telegram_bot_token), "email": bool(__import__("instilens.config", fromlist=["settings"]).settings.smtp_host)}}


@router.put("/me/settings")
def put_settings(body: NotifySettings, user: User = Depends(current_user), session: Session = Depends(get_session)):
    u = session.get(User, user.id)
    given = body.model_dump(exclude_unset=True)
    if "notify_email" in given:
        u.notify_email = bool(body.notify_email)
    if "notify_telegram_chat_id" in given:
        u.notify_telegram_chat_id = (body.notify_telegram_chat_id or "").strip() or None
    if "notify_brief" in given:
        u.notify_brief = bool(body.notify_brief)
    if "brief_markets" in given:
        markets = [m.upper() for m in (body.brief_markets or [])]
        if any(m not in ("TR", "US") for m in markets) or not markets:
            raise HTTPException(400, "brief_markets must be a non-empty subset of TR, US")
        u.brief_markets = sorted(set(markets))
    if body.lang:
        u.lang = body.lang
    return {"ok": True}


@router.post("/me/settings/test")
def test_notification(user: User = Depends(current_user), session: Session = Depends(get_session)):
    from instilens.services.notify import send_email, send_telegram

    u = session.get(User, user.id)
    out = {"telegram": None, "email": None}
    if u.notify_telegram_chat_id:
        out["telegram"] = send_telegram(u.notify_telegram_chat_id, "InstiLens bildirimleri bu sohbete gelecek ✅")
    if u.notify_email:
        out["email"] = send_email(u.email, "InstiLens test", "Bildirimler bu adrese gelecek.")
    return out


class PushSubBody(BaseModel):
    endpoint: str = Field(max_length=1024)
    keys: dict = Field(default_factory=dict)
    user_agent: str | None = Field(None, max_length=256)


@router.get("/push/public-key")
def push_public_key():
    from instilens.config import settings as _s

    return {"public_key": _s.vapid_public_key, "enabled": bool(_s.vapid_public_key), "onesignal_app_id": _s.onesignal_app_id}


@router.post("/push/subscribe", status_code=201)
def push_subscribe(body: PushSubBody, user: User = Depends(current_user), session: Session = Depends(get_session)):
    from sqlalchemy import select as _select

    from instilens.domain.models import PushSubscription

    sub = session.scalar(_select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if sub is None:
        sub = PushSubscription(owner_id=str(user.id), endpoint=body.endpoint, p256dh=body.keys.get("p256dh", ""), auth=body.keys.get("auth", ""), user_agent=body.user_agent)
        session.add(sub)
    elif sub.owner_id != str(user.id):
        raise HTTPException(409, "this device is registered to another account; unsubscribe there first")  # no endpoint takeover
    else:
        sub.p256dh, sub.auth, sub.user_agent = body.keys.get("p256dh", ""), body.keys.get("auth", ""), body.user_agent or sub.user_agent
    session.flush()
    return {"id": sub.id}


@router.delete("/push/subscribe", status_code=204)
def push_unsubscribe(endpoint: str, user: User = Depends(current_user), session: Session = Depends(get_session)):
    from sqlalchemy import delete as _delete

    from instilens.domain.models import PushSubscription

    session.execute(_delete(PushSubscription).where(PushSubscription.endpoint == endpoint, PushSubscription.owner_id == str(user.id)))


@router.post("/push/test")
def push_test(user: User = Depends(current_user), session: Session = Depends(get_session)):
    from instilens.config import settings as _s
    from instilens.services.notify import send_onesignal, send_push

    return {"sent": send_push(session, str(user.id), "InstiLens", "Push bildirimleri bu cihaza gelecek ✅", _s.public_url), "onesignal": send_onesignal(str(user.id), "InstiLens", "Push bildirimleri bu cihaza gelecek ✅", _s.public_url)}
