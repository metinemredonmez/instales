"""Password hashing (argon2id), password policy (NIST SP 800-63B-4), JWT issuance/verification, audit trail."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.api.hardening import failures, hit, reset_key
from instilens.config import settings
from instilens.domain.models import AuditEvent, User

log = logging.getLogger("instilens.auth")
_hasher = PasswordHasher()
ISSUER = "instilens"
AUDIENCE = "instilens-app"
TICKET_TTL = timedelta(minutes=5)


class AuthError(Exception):
    pass


class LockedOut(AuthError):
    pass


# ---------------------------------------------------------------- audit
def audit(session: Session, kind: str, *, actor: str | None = None, subject: str | None = None, ip: str | None = None, detail: str | None = None) -> None:
    session.add(AuditEvent(kind=kind, actor=actor, subject=subject, ip=ip, detail=(detail or "")[:512] or None))
    log.info("audit %s actor=%s subject=%s ip=%s %s", kind, actor, subject, ip, detail or "")


# ---------------------------------------------------------------- passwords
def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def _breached(password: str) -> bool:
    """HIBP k-anonymity: only the first 5 hex chars of the SHA-1 leave the server. Fail-open on network trouble."""
    if not settings.breached_password_check:
        return False
    sha = hashlib.sha1(password.encode()).hexdigest().upper()  # noqa: S324 — protocol-mandated
    try:
        r = httpx.get(f"https://api.pwnedpasswords.com/range/{sha[:5]}", headers={"Add-Padding": "true", "User-Agent": "InstiLens"}, timeout=3)
        r.raise_for_status()
    except httpx.HTTPError:
        return False
    return any(line.split(":")[0] == sha[5:] for line in r.text.splitlines())


def check_password_policy(password: str, email: str = "") -> None:
    """NIST 800-63B-4: length over composition; block context words and breached passwords."""
    if len(password) < 8:
        raise AuthError("password must be at least 8 characters")
    if len(password) > 128:
        raise AuthError("password must be at most 128 characters")
    low = password.lower()
    local = email.split("@")[0].lower() if email else ""
    if (local and len(local) >= 4 and local in low) or "instilens" in low:
        raise AuthError("password must not contain your e-mail or the product name")
    if len(set(low)) < 4:
        raise AuthError("password is too repetitive")
    if _breached(password):
        raise AuthError("this password appears in known data breaches; choose another")


# ---------------------------------------------------------------- accounts
def register(session: Session, email: str, password: str, name: str) -> User:
    email = email.strip().lower()
    check_password_policy(password, email)
    if session.scalar(select(User).where(User.email == email)):
        raise AuthError("email already registered")
    user = User(email=email, password_hash=hash_password(password), name=name.strip() or email.split("@")[0])
    session.add(user)
    session.flush()
    return user


def _lock_key(email: str) -> str:
    return f"acct:{email}"


def authenticate(session: Session, email: str, password: str, ip: str | None = None) -> User:
    """Per-account lockout on top of the IP limiter; the same generic message for every failure path."""
    email = email.strip().lower()
    window = settings.account_lockout_minutes * 60
    if failures(_lock_key(email), window) >= settings.account_lockout_attempts:
        audit(session, "auth.locked", actor=email, ip=ip)
        raise LockedOut("too many attempts for this account, try again later")
    user = session.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        hit(_lock_key(email), settings.account_lockout_attempts, window)  # only FAILED attempts count towards the lock
        audit(session, "auth.login_fail", actor=email, ip=ip)
        raise AuthError("invalid email or password")  # same message for both: no account enumeration
    reset_key(_lock_key(email))  # a correct password clears the counter
    user.last_login_at = datetime.now(UTC)
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    audit(session, "auth.login_ok", actor=email, ip=ip)
    return user


def change_password(session: Session, user: User, current: str, new: str, ip: str | None = None) -> None:
    if not verify_password(current, user.password_hash):
        audit(session, "auth.password_change_fail", actor=user.email, ip=ip)
        raise AuthError("current password is wrong")
    check_password_policy(new, user.email)
    user.password_hash = hash_password(new)
    user.token_version = (user.token_version or 1) + 1  # every other session dies
    audit(session, "auth.password_changed", actor=user.email, ip=ip)


def logout_everywhere(session: Session, user: User, ip: str | None = None) -> None:
    user.token_version = (user.token_version or 1) + 1
    audit(session, "auth.logout_all", actor=user.email, ip=ip)


# ---------------------------------------------------------------- tokens
def _claims(user: User, ttl: timedelta, scope: str) -> dict:
    now = datetime.now(UTC)
    return {"iss": ISSUER, "aud": AUDIENCE, "sub": str(user.id), "jti": uuid.uuid4().hex, "ver": user.token_version or 1,
            "scope": scope, "email": user.email, "plan": user.plan, "iat": now, "exp": now + ttl}


def issue_token(user: User) -> str:
    return jwt.encode(_claims(user, timedelta(minutes=settings.jwt_ttl_minutes), "session"), settings.jwt_secret, algorithm="HS256")


def issue_ticket(user: User) -> str:
    """Short-lived token for the two places a browser cannot send headers (EventSource, <audio>).
    Only accepted from the query string and only by those routes, so a leaked URL is worth 5 minutes."""
    return jwt.encode(_claims(user, TICKET_TTL, "ticket"), settings.jwt_secret, algorithm="HS256")


def user_from_token(session: Session, token: str, *, expect_scope: str = "session") -> User:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE,
                             options={"require": ["exp", "iat", "sub", "iss", "aud"]})
    except jwt.PyJWTError as exc:
        raise AuthError("invalid or expired token") from exc
    if payload.get("scope", "session") != expect_scope:
        raise AuthError("token not valid here")
    user = session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthError("user not found")
    if int(payload.get("ver", 1)) != int(user.token_version or 1):
        raise AuthError("session revoked")
    return user


def public_user(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "plan": user.plan, "role": user.role, "lang": user.lang}
