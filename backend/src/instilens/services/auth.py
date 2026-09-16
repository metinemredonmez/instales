"""Password hashing (argon2id) and JWT issuance/verification."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.models import User

_hasher = PasswordHasher()


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except VerifyMismatchError:
        return False


def register(session: Session, email: str, password: str, name: str) -> User:
    email = email.strip().lower()
    if len(password) < 8:
        raise AuthError("password must be at least 8 characters")
    if session.scalar(select(User).where(User.email == email)):
        raise AuthError("email already registered")
    user = User(email=email, password_hash=hash_password(password), name=name.strip() or email.split("@")[0])
    session.add(user)
    session.flush()
    return user


def authenticate(session: Session, email: str, password: str) -> User:
    user = session.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None or not user.is_active or not verify_password(password, user.password_hash):
        raise AuthError("invalid email or password")  # same message for both: no account enumeration
    user.last_login_at = datetime.now(UTC)
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    return user


def issue_token(user: User) -> str:
    now = datetime.now(UTC)
    payload = {"sub": str(user.id), "email": user.email, "plan": user.plan, "iat": now, "exp": now + timedelta(minutes=settings.jwt_ttl_minutes)}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def user_from_token(session: Session, token: str) -> User:
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError as exc:
        raise AuthError("invalid or expired token") from exc
    user = session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise AuthError("user not found")
    return user


def public_user(user: User) -> dict:
    return {"id": user.id, "email": user.email, "name": user.name, "plan": user.plan, "role": user.role, "lang": user.lang}
