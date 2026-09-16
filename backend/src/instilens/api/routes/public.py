"""Unauthenticated endpoints used by the public landing page (instilens.com)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.api.deps import get_session
from instilens.domain.models import WaitlistEntry

router = APIRouter(prefix="/api/v1/public", tags=["public"])


class WaitlistBody(BaseModel):
    email: EmailStr
    name: str | None = Field(None, max_length=128)
    lang: str = Field("tr", pattern="^(tr|en)$")
    source: str | None = Field(None, max_length=64)


@router.post("/waitlist", status_code=201)
def join_waitlist(body: WaitlistBody, session: Session = Depends(get_session)):
    """Early-access signup. Idempotent per e-mail; rate-limited by the auth limiter."""
    email = body.email.lower().strip()
    existing = session.scalar(select(WaitlistEntry).where(WaitlistEntry.email == email))
    if existing:
        return {"ok": True, "already": True}
    session.add(WaitlistEntry(email=email, name=(body.name or "").strip() or None, lang=body.lang, source=body.source))
    return {"ok": True, "already": False}
