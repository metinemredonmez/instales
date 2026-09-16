"""Deliver notifications and the morning brief over Telegram and e-mail. Descriptive text only."""

from __future__ import annotations

import logging
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.models import AiNote, Notification, PushSubscription, User

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
    if not (settings.smtp_host and settings.smtp_from):
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


def send_onesignal(owner_id: str, title: str, body: str, link: str) -> bool:
    """OneSignal push to the user (identified by external_id = our user id, set by OneSignal.login on the web)."""
    if not (settings.onesignal_app_id and settings.onesignal_rest_api_key):
        return False
    payload = {"app_id": settings.onesignal_app_id, "include_aliases": {"external_id": [owner_id]}, "target_channel": "push",
               "headings": {"en": title, "tr": title}, "contents": {"en": body, "tr": body}, "url": link,
               "chrome_web_icon": f"{settings.public_url}/icon-192.png", "firefox_icon": f"{settings.public_url}/icon-192.png"}
    for scheme in ("Key", "Basic"):  # new-style keys use "Key", legacy REST keys use "Basic"
        try:
            r = httpx.post("https://api.onesignal.com/notifications", json=payload, headers={"Authorization": f"{scheme} {settings.onesignal_rest_api_key}", "accept": "application/json"}, timeout=15)
        except httpx.HTTPError as exc:
            log.warning("onesignal failed: %s", exc)
            return False
        if r.status_code in (200, 201):
            data = r.json()
            return not data.get("errors")
        if r.status_code not in (401, 403):
            log.warning("onesignal %s: %s", r.status_code, r.text[:200])
            return False
    return False


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
        ok = False
        ok |= send_push(session, n.owner_id, n.title, n.body, link) > 0
        ok |= send_onesignal(n.owner_id, n.title, n.body, link)
        if u.notify_telegram_chat_id:
            ok |= send_telegram(u.notify_telegram_chat_id, f"<b>{n.title}</b>\n{n.body}\n{link}")
        if u.notify_email:
            ok |= send_email(u.email, f"InstiLens · {n.title}", f"{n.body}\n\n{link}\n\nBu bir veri bildirimidir, yatırım tavsiyesi değildir.")
        if ok or not (u.notify_telegram_chat_id or u.notify_email):
            n.delivered_at = datetime.now(UTC)
            sent += int(ok)
    session.flush()
    return sent


def deliver_brief(session: Session, note: AiNote) -> int:
    """Send the morning brief to every opted-in user, in the user's language (EN generated on demand)."""
    from instilens.ai.assess import daily_brief

    notes: dict[str, AiNote | None] = {note.lang: note}
    sent = 0
    for u in session.scalars(select(User).where(User.notify_brief.is_(True))):
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
        ok = False
        ok |= send_push(session, str(u.id), title, n.content[:180] + "…", settings.public_url) > 0
        ok |= send_onesignal(str(u.id), title, n.content[:180] + "…", settings.public_url)
        if u.notify_telegram_chat_id:
            ok |= send_telegram(u.notify_telegram_chat_id, f"<b>{title}</b>\n{body}")
        if u.notify_email:
            ok |= send_email(u.email, title, body)
        sent += int(ok)
    return sent
