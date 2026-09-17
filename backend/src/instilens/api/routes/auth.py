"""Account routes.

Session:   POST /register, POST /login (→ session, or {mfa_required, mfa_token} when the account has TOTP),
           POST /mfa/verify {mfa_token, code} → session, GET /me, POST /password, POST /logout-all, POST /ticket.
Recovery:  POST /forgot {email} → always 200; e-mails a single-use 30-minute link `{public_url}/reset?token=…` when SMTP is set.
           POST /reset {token, new_password} → new password (policy applies), every other session revoked. An account
           with TOTP gets the same {mfa_required, mfa_token} challenge as /login — mailbox access alone is not a login.
Address:   registration sends `{public_url}/verify?token=…`; POST /verify {token} flips users.email_verified.
           POST /verify/resend (signed in) mails a fresh link. Login is never blocked on verification; /me reports the flag.
MFA:       POST /mfa/setup → {secret, otpauth_uri}; POST /mfa/enable {code} confirms; POST /mfa/disable {code}.
Public:    GET /config → {allow_registration} so the login page can hide "Register" (no auth).
/forgot, /reset, /verify, /verify/resend and /mfa/verify are rate limited per IP (api/hardening.RATE_LIMITED_PATHS).
Every outgoing mail is queued, never sent inside the request (services.notify.queue_email).
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from instilens.api.deps import current_user, get_session
from instilens.api.hardening import client_ip
from instilens.config import settings
from instilens.domain.models import User
from instilens.services import auth, plans

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class Registration(Credentials):
    name: str = Field("", max_length=128)


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ForgotBody(BaseModel):
    email: EmailStr


class ResetBody(BaseModel):
    token: str = Field(min_length=16, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class TokenBody(BaseModel):
    token: str = Field(min_length=16, max_length=128)


class MfaCode(BaseModel):
    code: str = Field(min_length=6, max_length=8, pattern=r"^[0-9 ]+$")


class MfaVerify(MfaCode):
    mfa_token: str = Field(min_length=16, max_length=2048)


def _session_payload(user: User) -> dict:
    return {"access_token": auth.issue_token(user), "token_type": "bearer", "user": auth.public_user(user)}


def _mfa_challenge(user: User) -> dict:
    return {"mfa_required": True, "mfa_token": auth.issue_mfa_token(user), "ttl_seconds": int(auth.MFA_TTL.total_seconds())}


def _session_or_challenge(user: User) -> dict:
    """A session, unless the account carries a second factor — then the caller has to clear it first."""
    return _mfa_challenge(user) if auth.mfa_enabled(user) else _session_payload(user)


@router.post("/register", status_code=201)
def register(body: Registration, request: Request, session: Session = Depends(get_session)):
    if not settings.allow_registration:
        raise HTTPException(403, "registration is closed")
    try:
        user = auth.register(session, body.email, body.password, body.name)
    except auth.AuthError as exc:
        raise HTTPException(409, str(exc)) from exc
    auth.audit(session, "auth.registered", actor=user.email, ip=client_ip(request))
    auth.send_verification(session, user, ip=client_ip(request))  # silent when SMTP is not configured
    return _session_payload(user)


@router.get("/config")
def config():
    """Public: what the login page needs before anyone is signed in."""
    return {"allow_registration": settings.allow_registration}


@router.post("/login")
def login(body: Credentials, request: Request, session: Session = Depends(get_session)):
    try:
        user = auth.authenticate(session, body.email, body.password, ip=client_ip(request))
    except auth.LockedOut as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(settings.account_lockout_minutes * 60)}) from exc
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    return _session_or_challenge(user)


@router.post("/mfa/verify")
def mfa_verify(body: MfaVerify, request: Request, session: Session = Depends(get_session)):
    """Second login step for accounts with TOTP: the mfa_token from /login plus the current code → session."""
    try:
        user = auth.mfa_verify(session, body.mfa_token, body.code, ip=client_ip(request))
    except auth.LockedOut as exc:
        raise HTTPException(429, str(exc), headers={"Retry-After": str(settings.account_lockout_minutes * 60)}) from exc
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    return _session_payload(user)


@router.post("/forgot")
def forgot(body: ForgotBody, request: Request, session: Session = Depends(get_session)):
    """Always 200 — whether the address exists is never revealed. The mail carries a single-use 30-minute link."""
    auth.request_password_reset(session, body.email, ip=client_ip(request))
    return {"ok": True}


@router.post("/reset")
def reset(body: ResetBody, request: Request, session: Session = Depends(get_session)):
    """Consume the e-mailed token, set the new password (policy applies) and revoke every other session.
    Mailbox access is only the first factor: an account with TOTP is handed the same challenge /login returns."""
    try:
        user = auth.reset_password(session, body.token, body.new_password, ip=client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    session.flush()
    return _session_or_challenge(user)


@router.post("/verify")
def verify(body: TokenBody, request: Request, session: Session = Depends(get_session)):
    try:
        user = auth.verify_email(session, body.token, ip=client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "email_verified": True, "email": user.email}


@router.post("/verify/resend")
def verify_resend(request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Mail a fresh verification link to the signed-in account (the previous one dies). Silent without SMTP."""
    u = session.get(User, user.id)
    return {"ok": True, "sent": auth.send_verification(session, u, ip=client_ip(request)), "email_verified": bool(u.email_verified)}


@router.post("/mfa/setup")
def mfa_setup(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Start TOTP enrolment: returns the secret and the otpauth:// URI for the authenticator app (QR on the client).
    Nothing is enforced until /mfa/enable confirms a code. Intended for ADMIN accounts; any account may enrol."""
    try:
        return auth.mfa_setup(session, session.get(User, user.id))
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/mfa/enable")
def mfa_enable(body: MfaCode, request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        auth.mfa_enable(session, session.get(User, user.id), body.code, ip=client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "mfa_enabled": True}


@router.post("/mfa/disable")
def mfa_disable(body: MfaCode, request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        auth.mfa_disable(session, session.get(User, user.id), body.code, ip=client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "mfa_enabled": False}


@router.get("/me")
def me(user: User = Depends(current_user), session: Session = Depends(get_session)):
    """The account plus its plan block (services/plans.me_payload): `plan` is the plan in effect (own or inherited
    from an organisation), `own_plan` the account's own, `features` the matrix that applies, `plans_enforced` the
    switch — so the UI can show limits whether or not they are enforced yet."""
    return {**auth.public_user(user), **plans.me_payload(session, user)}


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
