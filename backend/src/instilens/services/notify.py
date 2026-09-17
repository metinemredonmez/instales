"""Deliver notifications and the morning brief over Telegram and e-mail. Descriptive text only."""

from __future__ import annotations

import logging
import queue
import smtplib
import threading
import time
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.enums import DeliveryChannel
from instilens.domain.models import AiNote, BriefDelivery, Notification, PushSubscription, User

log = logging.getLogger(__name__)


def send_telegram(chat_id: str, text: str) -> bool:
    if not settings.telegram_bot_token:
        return False
    try:
        r = httpx.post(f"https://api.telegram.org/bot{settings.telegram_bot_token}/sendMessage", json={"chat_id": chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}, timeout=15)
        return r.status_code == 200
    except httpx.HTTPError as exc:
        log.warning("telegram failed: %s", exc)
        return False


def send_email(to: str, subject: str, body: str) -> bool:
    """Blocking SMTP send. Use queue_email() from anything that answers an HTTP request."""
    if not smtp_configured():
        return False
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = settings.smtp_from, to, subject
    msg.set_content(body)
    try:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as s:
            s.starttls()
            if settings.smtp_user:
                s.login(settings.smtp_user, settings.smtp_password or "")
            s.send_message(msg)
        return True
    except (smtplib.SMTPException, OSError) as exc:
        log.warning("email failed: %s", exc)
        return False


def smtp_configured() -> bool:
    return bool(settings.smtp_host and settings.smtp_from)


# ---------------------------------------------------------------- out-of-band mail
# smtplib blocks for up to 20 s. Inside a request that is two bugs at once: it pins a threadpool worker
# (100 unauthenticated /auth/forgot calls a minute can starve the pool) and it makes the response measurably
# slower for addresses that exist — the account-enumeration oracle /auth/forgot is built to close. So request
# handlers enqueue and return; one daemon thread drains the queue.
_MAILQ: queue.Queue[tuple[str, str, str]] = queue.Queue(maxsize=500)
_mail_worker: threading.Thread | None = None
_mail_lock = threading.Lock()


def _drain_mail() -> None:
    while True:
        to, subject, body = _MAILQ.get()
        try:
            send_email(to, subject, body)  # module attribute on purpose: tests capture the outbox here
        except Exception:  # noqa: BLE001 — a bad message must never kill the sender thread
            log.exception("mail worker failed for %s", to)
        finally:
            _MAILQ.task_done()


def queue_email(to: str, subject: str, body: str) -> bool:
    """Hand a message to the background sender and return at once. True = SMTP is configured and it was queued."""
    if not smtp_configured():
        return False
    global _mail_worker
    with _mail_lock:
        if _mail_worker is None or not _mail_worker.is_alive():
            _mail_worker = threading.Thread(target=_drain_mail, name="instilens-mail", daemon=True)
            _mail_worker.start()
    try:
        _MAILQ.put_nowait((to, subject, body))
    except queue.Full:
        log.warning("mail queue full; dropped message to %s", to)
        return False
    return True


def flush_mail(timeout: float = 5.0) -> bool:
    """Block until the queue is drained (tests, graceful shutdown). False if it was still busy at `timeout`."""
    deadline = time.monotonic() + timeout
    while not _MAILQ.empty() and time.monotonic() < deadline:
        time.sleep(0.01)
    return _MAILQ.empty()


def send_push(session: Session, owner_id: str, title: str, body: str, link: str) -> int:
    """Web Push to every device of the user; dead subscriptions (410/404) are removed."""
    if not (settings.vapid_private_key and settings.vapid_public_key):
        return 0
    import json

    from pywebpush import WebPushException, webpush

    sent = 0
    for sub in session.scalars(select(PushSubscription).where(PushSubscription.owner_id == owner_id)):
        try:
            webpush(
                subscription_info={"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}},
                data=json.dumps({"title": title, "body": body, "url": link}),
                vapid_private_key=settings.vapid_private_key,
                vapid_claims={"sub": settings.vapid_subject},
                ttl=3600,
            )
            sub.last_ok_at = datetime.now(UTC)
            sent += 1
        except WebPushException as exc:
            code = getattr(exc.response, "status_code", None)
            if code in (404, 410):
                session.delete(sub)
            else:
                log.warning("push failed: %s", exc)
    return sent


LAST_ONESIGNAL_ERROR: dict[str, str] = {}  # owner_id → last failure reason (surfaced by /push/test)


def onesignal_external_id(owner_id: str) -> str:
    """OneSignal alias for a user. Bare ids like "1" are on OneSignal's blocklist ("external_id is blocked"), so the
    id is prefixed; the web SDK logs in with the same value — keep in sync with oneSignalExternalId() in
    frontend/src/lib/onesignal.ts."""
    return f"instilens-{owner_id}"


def send_onesignal(owner_id: str, title: str, body: str, link: str) -> bool:
    """OneSignal push to the user (identified by external_id = onesignal_external_id(), set by OneSignal.login on the web).
    Every failure path is logged and remembered so the settings page can say WHY a test did not arrive."""
    if not (settings.onesignal_app_id and settings.onesignal_rest_api_key):
        LAST_ONESIGNAL_ERROR[owner_id] = "not configured"
        return False
    payload = {"app_id": settings.onesignal_app_id, "include_aliases": {"external_id": [onesignal_external_id(owner_id)]}, "target_channel": "push",
               "headings": {"en": title, "tr": title}, "contents": {"en": body, "tr": body}, "url": link,
               "chrome_web_icon": f"{settings.public_url}/icon-192.png", "firefox_icon": f"{settings.public_url}/icon-192.png"}
    last = ""
    for scheme in ("Key", "Basic"):  # new-style keys use "Key", legacy REST keys use "Basic"
        try:
            r = httpx.post("https://api.onesignal.com/notifications", json=payload, headers={"Authorization": f"{scheme} {settings.onesignal_rest_api_key}", "accept": "application/json"}, timeout=15)
        except httpx.HTTPError as exc:
            log.warning("onesignal failed: %s", exc)
            LAST_ONESIGNAL_ERROR[owner_id] = f"network: {exc}"
            return False
        if r.status_code in (200, 201):
            data = r.json()
            errors = data.get("errors")
            if errors:
                # typical: {"invalid_aliases": {"external_id": ["instilens-1"]}} → the browser never linked this user id
                log.warning("onesignal rejected user %s: %s", owner_id, errors)
                LAST_ONESIGNAL_ERROR[owner_id] = f"no subscription for this user ({errors})"[:200]
                return False
            LAST_ONESIGNAL_ERROR.pop(owner_id, None)
            return True
        last = f"{r.status_code} {r.text[:160]}"
        if r.status_code not in (401, 403):
            break
    log.warning("onesignal %s", last)
    LAST_ONESIGNAL_ERROR[owner_id] = f"rejected: {last}"
    return False


def onesignal_configured() -> bool:
    return bool(settings.onesignal_app_id and settings.onesignal_rest_api_key)


def push_user(session: Session, owner_id: str, title: str, body: str, link: str) -> str | None:
    """One push path per user: OneSignal when it is configured (and reaches the user), otherwise VAPID.
    Never both, so a phone with both registrations does not buzz twice. Returns the channel that delivered.
    A plan without `push` (services/plans, while enforced) gets none on either path — a device registered
    earlier, or straight with OneSignal, follows the plan like a new one."""
    from instilens.services import plans

    if not plans.allows(session, owner_id, "push"):
        return None
    if onesignal_configured() and send_onesignal(owner_id, title, body, link):
        return DeliveryChannel.ONESIGNAL.value
    if send_push(session, owner_id, title, body, link) > 0:
        return DeliveryChannel.PUSH.value
    return None


def deliver_pending(session: Session) -> int:
    """Send every undelivered notification to its owner's configured channels."""
    sent = 0
    users = {str(u.id): u for u in session.scalars(select(User))}
    for n in session.scalars(select(Notification).where(Notification.delivered_at.is_(None)).order_by(Notification.id)):
        u = users.get(n.owner_id)
        if u is None:
            n.delivered_at = datetime.now(UTC)
            continue
        link = f"{settings.public_url}{n.link}" if n.link else settings.public_url
        ok = push_user(session, n.owner_id, n.title, n.body, link) is not None
        if u.notify_telegram_chat_id:
            ok |= send_telegram(u.notify_telegram_chat_id, f"<b>{n.title}</b>\n{n.body}\n{link}")
        if u.notify_email:
            ok |= send_email(u.email, f"InstiLens · {n.title}", f"{n.body}\n\n{link}\n\nBu bir veri bildirimidir, yatırım tavsiyesi değildir.")
        if ok or not (u.notify_telegram_chat_id or u.notify_email):
            n.delivered_at = datetime.now(UTC)
            sent += int(ok)
    session.flush()
    return sent


def user_brief_markets(u: User) -> list[str]:
    markets = [m for m in (u.brief_markets or []) if m in ("TR", "US")]
    return markets or ["TR"]


def _delivered(session: Session, u: User, market: str, day, lang: str) -> set[str]:
    rows = session.scalars(select(BriefDelivery.channel).where(BriefDelivery.user_id == u.id, BriefDelivery.market == market, BriefDelivery.day == day, BriefDelivery.lang == lang))
    return set(rows)


def deliver_brief(session: Session, note: AiNote) -> int:
    """Send the morning brief to every user who opted in for this market, in the user's language (EN generated on
    demand). Every (user, market, day, lang, channel) delivery is recorded in `brief_deliveries`, so running the
    08:30 job twice never sends the same brief twice. Returns the number of users reached for the first time."""
    from instilens.ai.assess import daily_brief

    notes: dict[str, AiNote | None] = {note.lang: note}
    sent = 0
    for u in session.scalars(select(User).where(User.notify_brief.is_(True), User.is_active.is_(True))):
        if note.market_code not in user_brief_markets(u):
            continue
        lang = u.lang if u.lang in ("tr", "en") else "tr"
        if lang not in notes:
            try:
                notes[lang] = daily_brief(session, note.market_code, day=note.as_of, lang=lang)
            except Exception as exc:  # noqa: BLE001 — never let one language break delivery
                log.warning("brief %s/%s failed: %s", note.market_code, lang, exc)
                notes[lang] = None
        n = notes.get(lang) or note
        if n.lang == "en":
            title = "InstiLens morning brief · " + ("Turkey" if n.market_code == "TR" else "Global")
            footer = "Descriptive AI note; not investment advice."
        else:
            title = "InstiLens sabah brifingi · " + ("Türkiye" if n.market_code == "TR" else "Global")
            footer = "Betimleyici AI notu; yatırım tavsiyesi değildir."
        watch = "\n".join(f"• {w}" for w in (n.data or {}).get("watch", []))
        body = f"{n.content}\n\n{watch}\n\n{settings.public_url}\n{footer}"
        done = _delivered(session, u, n.market_code, n.as_of, n.lang)
        got: list[str] = []
        if not done & {DeliveryChannel.PUSH, DeliveryChannel.ONESIGNAL}:
            ch = push_user(session, str(u.id), title, n.content[:180] + "…", settings.public_url)
            if ch:
                got.append(ch)
        if u.notify_telegram_chat_id and DeliveryChannel.TELEGRAM not in done and send_telegram(u.notify_telegram_chat_id, f"<b>{title}</b>\n{body}"):
            got.append(DeliveryChannel.TELEGRAM.value)
        if u.notify_email and DeliveryChannel.EMAIL not in done and send_email(u.email, title, body):
            got.append(DeliveryChannel.EMAIL.value)
        for ch in got:
            session.add(BriefDelivery(user_id=u.id, market=n.market_code, day=n.as_of, lang=n.lang, channel=ch))
        if got and not done:
            sent += 1
    session.flush()
    return sent
