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
from instilens.domain.models import AiNote, Notification, User

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
    sent = 0
    title = "InstiLens sabah brifingi · " + ("Türkiye" if note.market_code == "TR" else "Global")
    watch = "\n".join(f"• {w}" for w in (note.data or {}).get("watch", []))
    body = f"{note.content}\n\n{watch}\n\n{settings.public_url}\nBetimleyici AI notu; yatırım tavsiyesi değildir."
    for u in session.scalars(select(User).where(User.notify_brief.is_(True))):
        ok = False
        if u.notify_telegram_chat_id:
            ok |= send_telegram(u.notify_telegram_chat_id, f"<b>{title}</b>\n{body}")
        if u.notify_email:
            ok |= send_email(u.email, title, body)
        sent += int(ok)
    return sent
