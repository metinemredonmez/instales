"""Password hashing (argon2id), password policy (NIST SP 800-63B-4), JWT issuance/verification, audit trail,
e-mailed single-use tokens (password reset, address verification) and TOTP second factor."""

from __future__ import annotations

import hashlib
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.api.hardening import failures, hit, reset_key
from instilens.config import settings
from instilens.domain.enums import AuthTokenKind
from instilens.domain.models import AuditEvent, AuthToken, User

log = logging.getLogger("instilens.auth")
_hasher = PasswordHasher()
ISSUER = "instilens"
AUDIENCE = "instilens-app"
TICKET_TTL = timedelta(minutes=5)
MFA_TTL = timedelta(minutes=5)  # between a correct password and the TOTP code
RESET_TTL = timedelta(minutes=30)
VERIFY_TTL = timedelta(hours=24)
_PENDING = "pending:"  # totp_secret prefix while the user has scanned the QR but not confirmed a code yet


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
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    if mfa_enabled(user):
        # The password is only half of this login: recording it as a success would put "auth.login_ok" and a
        # fresh last_login_at in the trail for anyone who merely knows the password. mfa_verify closes the login.
        audit(session, "auth.login_password_ok", actor=email, ip=ip, detail="awaiting mfa")
        return user
    _login_succeeded(session, user, ip)
    return user


def _login_succeeded(session: Session, user: User, ip: str | None) -> None:
    """The one place a completed login is stamped and audited (after TOTP when the account has it)."""
    user.last_login_at = datetime.now(UTC)
    audit(session, "auth.login_ok", actor=user.email, ip=ip)


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


# ---------------------------------------------------------------- e-mailed single-use tokens
def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def issue_email_token(session: Session, user: User, kind: str, ttl: timedelta) -> str:
    """Mint a one-shot token for `user`; only its SHA-256 is stored. Earlier live tokens of the same kind die."""
    now = datetime.now(UTC)
    for old in session.scalars(select(AuthToken).where(AuthToken.user_id == user.id, AuthToken.kind == kind, AuthToken.used_at.is_(None))):
        old.used_at = now
    return mint_token(session, user, kind, ttl)


def mint_token(session: Session, user: User, kind: str, ttl: timedelta, subject: str | None = None) -> str:
    """A one-shot token for `user` that leaves earlier live tokens of the kind alone — an organisation owner has one
    open ORG_INVITE link per invitee (services/org); reset and verify links go through issue_email_token. `subject`
    ties the token to one thing (the invitation row), so it cannot be spent on another."""
    token = secrets.token_urlsafe(32)
    session.add(AuthToken(kind=kind, token_hash=_token_hash(token), user_id=user.id, expires_at=datetime.now(UTC) + ttl, subject=subject))
    session.flush()
    return token


def _live_token(session: Session, kind: str, token: str) -> tuple[AuthToken, User]:
    """A stored, unused, unexpired token of `kind` and its active user; the same generic error for every failure path."""
    row = session.scalar(select(AuthToken).where(AuthToken.token_hash == _token_hash(token), AuthToken.kind == kind))
    if row is None or row.used_at is not None or row.expires_at.replace(tzinfo=UTC) < datetime.now(UTC):
        raise AuthError("invalid or expired link")
    user = session.get(User, row.user_id)
    if user is None or not user.is_active:
        raise AuthError("invalid or expired link")
    return row, user


def consume_email_token(session: Session, kind: str, token: str) -> User:
    """Validate and burn a token (single use)."""
    row, user = _live_token(session, kind, token)
    row.used_at = datetime.now(UTC)
    return user


def peek_token(session: Session, kind: str, token: str) -> tuple[AuthToken, User]:
    """Validate without burning: the caller sets `used_at` once its own checks pass (an organisation invitation is
    only consumed by the invited address, services/org.accept)."""
    return _live_token(session, kind, token)


def _mail(user: User, subject_tr: str, subject_en: str, body_tr: str, body_en: str) -> bool:
    """Queue the mail; SMTP never runs inside the request (see services.notify.queue_email)."""
    from instilens.services import notify

    if user.lang == "en":
        return notify.queue_email(user.email, subject_en, body_en)
    return notify.queue_email(user.email, subject_tr, body_tr)


def request_password_reset(session: Session, email: str, ip: str | None = None) -> bool:
    """Always silent towards the caller; True only when a reset mail was queued (the send itself is out of band,
    so the response time is the same for an address that exists and one that does not)."""
    email = email.strip().lower()
    user = session.scalar(select(User).where(User.email == email))
    if user is None or not user.is_active:
        audit(session, "auth.reset_requested", actor=email, ip=ip, detail="no such account")
        return False
    link = f"{settings.public_url}/reset?token={issue_email_token(session, user, AuthTokenKind.RESET, RESET_TTL)}"
    audit(session, "auth.reset_requested", actor=email, ip=ip)
    mins = int(RESET_TTL.total_seconds() // 60)
    return _mail(user, "InstiLens · şifre sıfırlama", "InstiLens · password reset",
                 f"Şifreni sıfırlamak için bu bağlantıyı aç ({mins} dakika geçerli, tek kullanımlık):\n\n{link}\n\nBu isteği sen yapmadıysan bu e-postayı yok sayabilirsin.",
                 f"Open this link to reset your password (valid {mins} minutes, single use):\n\n{link}\n\nIf you did not ask for this, ignore this e-mail.")


def reset_password(session: Session, token: str, new_password: str, ip: str | None = None) -> User:
    row, user = _live_token(session, AuthTokenKind.RESET, token)
    check_password_policy(new_password, user.email)  # a rejected password must not burn the link
    row.used_at = datetime.now(UTC)
    user.password_hash = hash_password(new_password)
    user.token_version = (user.token_version or 1) + 1  # every existing session dies
    reset_key(_lock_key(user.email))  # a reset clears a lockout
    user.email_verified = True  # the link only reaches the mailbox, which is exactly what /verify proves
    audit(session, "auth.password_reset", actor=user.email, ip=ip)
    return user


def send_verification(session: Session, user: User, ip: str | None = None) -> bool:
    """Mail the address-verification link; login is never blocked on it.
    Without SMTP no token is minted at all — an unusable single-use row per registration is just litter."""
    from instilens.services.notify import smtp_configured

    if user.email_verified or not smtp_configured():
        return False
    link = f"{settings.public_url}/verify?token={issue_email_token(session, user, AuthTokenKind.VERIFY, VERIFY_TTL)}"
    audit(session, "auth.verify_sent", actor=user.email, ip=ip)
    return _mail(user, "InstiLens · e-posta doğrulama", "InstiLens · verify your e-mail",
                 f"E-posta adresini doğrulamak için bu bağlantıyı aç (24 saat geçerli):\n\n{link}",
                 f"Open this link to verify your e-mail address (valid 24 hours):\n\n{link}")


def verify_email(session: Session, token: str, ip: str | None = None) -> User:
    user = consume_email_token(session, AuthTokenKind.VERIFY, token)
    user.email_verified = True
    audit(session, "auth.email_verified", actor=user.email, ip=ip)
    return user


# ---------------------------------------------------------------- TOTP second factor
def mfa_enabled(user: User) -> bool:
    return bool(user.totp_secret) and not user.totp_secret.startswith(_PENDING)


def _totp(secret: str) -> pyotp.TOTP:
    return pyotp.TOTP(secret)


def mfa_setup(session: Session, user: User) -> dict:
    """New pending secret + otpauth URI for the authenticator app. Enabling requires one valid code (mfa_enable)."""
    if mfa_enabled(user):
        raise AuthError("mfa is already enabled; disable it first")
    secret = pyotp.random_base32()
    user.totp_secret = _PENDING + secret
    session.flush()
    return {"secret": secret, "otpauth_uri": _totp(secret).provisioning_uri(name=user.email, issuer_name="InstiLens")}


def mfa_enable(session: Session, user: User, code: str, ip: str | None = None) -> None:
    if not user.totp_secret or not user.totp_secret.startswith(_PENDING):
        raise AuthError("run mfa setup first")
    secret = user.totp_secret[len(_PENDING):]
    if not _totp(secret).verify(code.strip(), valid_window=1):
        audit(session, "auth.mfa_enable_fail", actor=user.email, ip=ip)
        raise AuthError("wrong code")
    user.totp_secret = secret
    audit(session, "auth.mfa_enabled", actor=user.email, ip=ip)


def mfa_disable(session: Session, user: User, code: str, ip: str | None = None) -> None:
    if not mfa_enabled(user):
        raise AuthError("mfa is not enabled")
    if not _totp(user.totp_secret).verify(code.strip(), valid_window=1):
        audit(session, "auth.mfa_disable_fail", actor=user.email, ip=ip)
        raise AuthError("wrong code")
    user.totp_secret = None
    audit(session, "auth.mfa_disabled", actor=user.email, ip=ip)


def _mfa_key(user: User) -> str:
    return f"mfa:{user.id}"


def mfa_verify(session: Session, mfa_token: str, code: str, ip: str | None = None) -> User:
    """Second step of login: the 5-minute mfa token from /login plus a TOTP code. Wrong codes count towards the
    account lock, and the mfa token is single-use — otherwise it mints unlimited sessions for its whole window."""
    payload, user = _decode(session, mfa_token, expect_scope="mfa")
    window = settings.account_lockout_minutes * 60
    if failures(_mfa_key(user), window) >= settings.account_lockout_attempts:
        audit(session, "auth.locked", actor=user.email, ip=ip, detail="mfa")
        raise LockedOut("too many attempts for this account, try again later")
    if not mfa_enabled(user) or not _totp(user.totp_secret).verify(code.strip(), valid_window=1):
        hit(_mfa_key(user), settings.account_lockout_attempts, window)
        audit(session, "auth.mfa_fail", actor=user.email, ip=ip)
        raise AuthError("wrong code")
    if not hit(f"mfajti:{payload['jti']}", 1, MFA_TTL.total_seconds()):  # burn it: one mfa token, one session
        audit(session, "auth.mfa_replay", actor=user.email, ip=ip)
        raise AuthError("this code has already been used")
    reset_key(_mfa_key(user))
    _login_succeeded(session, user, ip)
    audit(session, "auth.mfa_ok", actor=user.email, ip=ip)
    return user


# ---------------------------------------------------------------- tokens
def _claims(user: User, ttl: timedelta, scope: str) -> dict:
    now = datetime.now(UTC)
    return {"iss": ISSUER, "aud": AUDIENCE, "sub": str(user.id), "jti": uuid.uuid4().hex, "ver": user.token_version or 1,
            "scope": scope, "email": user.email, "plan": user.plan, "iat": now, "exp": now + ttl}


def issue_token(user: User) -> str:
    return jwt.encode(_claims(user, timedelta(minutes=settings.jwt_ttl_minutes), "session"), settings.jwt_secret, algorithm="HS256")


def issue_mfa_token(user: User) -> str:
    """Proof that the password step passed; only /auth/mfa/verify accepts it."""
    return jwt.encode(_claims(user, MFA_TTL, "mfa"), settings.jwt_secret, algorithm="HS256")


def issue_ticket(user: User) -> str:
    """Short-lived token for the two places a browser cannot send headers (EventSource, <audio>).
    Only accepted from the query string and only by those routes, so a leaked URL is worth 5 minutes."""
    return jwt.encode(_claims(user, TICKET_TTL, "ticket"), settings.jwt_secret, algorithm="HS256")


def _decode(session: Session, token: str, *, expect_scope: str = "session") -> tuple[dict, User]:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"], issuer=ISSUER, audience=AUDIENCE,
                             options={"require": ["exp", "iat", "sub", "iss", "aud", "jti"]})
    except jwt.PyJWTError as exc:
        raise AuthError("invalid or expired token") from exc
    if payload.get("scope", "session") != expect_scope:
        raise AuthError("token not valid here")
    user = session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthError("user not found")
    if int(payload.get("ver", 1)) != int(user.token_version or 1):
        raise AuthError("session revoked")
    return payload, user


def user_from_token(session: Session, token: str, *, expect_scope: str = "session") -> User:
    return _decode(session, token, expect_scope=expect_scope)[1]


def public_user(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "plan": user.plan, "role": user.role, "lang": user.lang,
            "email_verified": bool(user.email_verified), "mfa_enabled": mfa_enabled(user)}
