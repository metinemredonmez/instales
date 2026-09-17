"""Organisations (services/org): one per owner, members inherit the owner's plan.

GET    /org                       the organisation the user owns or belongs to; null when none
POST   /org {name}                create; the caller becomes owner and first member (402 plan_limit org_seats when the plan has no seats)
PATCH  /org {name}                rename (owner)
DELETE /org                       close the organisation (owner); every member loses the inherited plan
POST   /org/invite {email}        mail an invitation link (owner; seat cap while plans are enforced)
POST   /org/accept {token}        accept an invitation with the signed-in account (its e-mail must be the invited one)
DELETE /org/members/{member_id}   remove a member or a pending invitation (owner), or leave (the member's own row)
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.orm import Session

from instilens.api.deps import current_user, get_session
from instilens.api.hardening import client_ip
from instilens.domain.models import Organization, User
from instilens.services import auth, org

router = APIRouter(prefix="/api/v1/org", tags=["org"], dependencies=[Depends(current_user)])


class OrgBody(BaseModel):
    name: str = Field(min_length=2, max_length=64)


class InviteBody(BaseModel):
    email: EmailStr


class TokenBody(BaseModel):
    token: str = Field(min_length=16, max_length=128)


def _mine(session: Session, user: User) -> Organization:
    o = org.of_user(session, user)
    if o is None:
        raise HTTPException(404, "no organisation")
    return o


@router.get("")
def get_org(user: User = Depends(current_user), session: Session = Depends(get_session)):
    o = org.of_user(session, user)
    return org.payload(session, o, user) if o else None


@router.post("", status_code=201)
def create_org(body: OrgBody, user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        o = org.create(session, session.get(User, user.id), body.name)
    except org.OrgError as exc:
        raise HTTPException(409, str(exc)) from exc
    return org.payload(session, o, user)


@router.patch("")
def rename_org(body: OrgBody, user: User = Depends(current_user), session: Session = Depends(get_session)):
    o = _mine(session, user)
    try:
        org.rename(session, o, user, body.name)
    except org.OrgError as exc:
        raise HTTPException(403, str(exc)) from exc
    return org.payload(session, o, user)


@router.delete("", status_code=204)
def delete_org(user: User = Depends(current_user), session: Session = Depends(get_session)):
    o = _mine(session, user)
    try:
        org.delete(session, o, user)
    except org.OrgError as exc:
        raise HTTPException(403, str(exc)) from exc


@router.post("/invite", status_code=201)
def invite(body: InviteBody, request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Queue the invitation mail (silent when SMTP is not configured: `sent: false`, the seat is still reserved)."""
    o = _mine(session, user)
    try:
        member, sent = org.invite(session, o, session.get(User, user.id), body.email, ip=client_ip(request))
    except org.OrgError as exc:
        raise HTTPException(409, str(exc)) from exc
    return {"member_id": member.id, "email": member.invited_email, "sent": sent, "org": org.payload(session, o, user)}


@router.post("/accept")
def accept(body: TokenBody, request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)):
    try:
        o = org.accept(session, session.get(User, user.id), body.token, ip=client_ip(request))
    except auth.AuthError as exc:
        raise HTTPException(400, str(exc)) from exc
    except org.OrgError as exc:
        raise HTTPException(409, str(exc)) from exc
    return org.payload(session, o, user)


@router.delete("/members/{member_id}", status_code=204)
def remove_member(member_id: int, user: User = Depends(current_user), session: Session = Depends(get_session)):
    o = _mine(session, user)
    try:
        org.remove(session, o, user, member_id)
    except org.OrgError as exc:
        raise HTTPException(404 if str(exc) == "not found" else 403, str(exc)) from exc
