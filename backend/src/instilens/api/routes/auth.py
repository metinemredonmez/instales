from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from instilens.api.deps import current_user, get_session
from instilens.config import settings
from instilens.domain.models import User
from instilens.services import auth

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


class Credentials(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class Registration(Credentials):
    name: str = Field("", max_length=128)


def _session_payload(user: User) -> dict:
    return {"access_token": auth.issue_token(user), "token_type": "bearer", "user": auth.public_user(user)}


@router.post("/register", status_code=201)
def register(body: Registration, session: Session = Depends(get_session)):
    if not settings.allow_registration:
        raise HTTPException(403, "registration is closed")
    try:
        user = auth.register(session, body.email, body.password, body.name)
    except auth.AuthError as exc:
        raise HTTPException(409, str(exc)) from exc
    return _session_payload(user)


@router.post("/login")
def login(body: Credentials, session: Session = Depends(get_session)):
    try:
        user = auth.authenticate(session, body.email, body.password)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    return _session_payload(user)


@router.get("/me")
def me(user: User = Depends(current_user)):
    return auth.public_user(user)
