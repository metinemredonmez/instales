from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from instilens.api.deps import current_user, get_session
from instilens.api.hardening import client_ip
from instilens.config import settings
from instilens.domain.models import User
from instilens.services import auth

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class Registration(Credentials):
    name: str = Field("", max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


def _session_payload(user: User) -> dict:
    return {"access_token": auth.issue_token(user), "token_type": "bearer", "user": auth.public_user(user)}


@router.post("/register", status_code=201)
def register(body: Registration, request: Request, session: Session = Depends(get_session)):
    if not settings.allow_registration:
        raise HTTPException(403, "registration is closed")
    try:
        user = auth.register(session, body.email, body.password, body.name)
    except auth.AuthError as exc:
        raise HTTPException(409, str(exc)) from exc
    auth.audit(session, "auth.registered", actor=user.email, ip=client_ip(request))
    return _session_payload(user)


@router.post("/login")
def login(body: Credentials, request: Request, session: Session = Depends(get_session)):
    try:
        user = auth.authenticate(session, body.email, body.password, ip=client_ip(request))
    except auth.LockedOut as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(settings.account_lockout_minutes * 60)}) from exc
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    return _session_payload(user)


@router.get("/me")
def me(user: User = Depends(current_user)):
    return auth.public_user(user)


@router.post("/password")
def change_password(body: PasswordChange, request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Change password; every other session is revoked and a fresh token is returned for this one."""
    u = session.get(User, user.id)
    try:
        auth.change_password(session, u, body.current_password, body.new_password, ip=client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.flush()
    return _session_payload(u)


@router.post("/logout-all")
def logout_all(request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Revoke every token of this account (this one included)."""
    auth.logout_everywhere(session, session.get(User, user.id), ip=client_ip(request))
    return {"ok": True}


@router.post("/ticket")
def ticket(user: User = Depends(current_user)):
    """5-minute token for EventSource / <audio> URLs (the only places a query-string credential is accepted)."""
    return {"ticket": auth.issue_ticket(user), "ttl_seconds": int(auth.TICKET_TTL.total_seconds())}
