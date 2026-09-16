from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from instilens.api.deps import current_user, get_session
from instilens.domain.models import User
from instilens.services import admin


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(403, "admin only")
    return user


router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class UserPatch(BaseModel):
    plan: str | None = Field(None, pattern="^(FREE|PRO|PRO_PLUS)$")
    role: str | None = Field(None, pattern="^(USER|ADMIN)$")
    is_active: bool | None = None


class VerifyBody(BaseModel):
    kind: str = Field(pattern="^(instrument|fund|institution)$")
    id: int
    name: str | None = Field(None, max_length=256)


@router.get("/users")
def users(session: Session = Depends(get_session)):
    return admin.list_users(session)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, session: Session = Depends(get_session)):
    out = admin.update_user(session, user_id, plan=body.plan, role=body.role, is_active=body.is_active)
    if out is None:
        raise HTTPException(404, "not found")
    return out


@router.get("/review")
def review(session: Session = Depends(get_session)):
    return admin.unverified(session)


@router.post("/review/verify")
def verify(body: VerifyBody, session: Session = Depends(get_session)):
    if not admin.verify(session, body.kind, body.id, body.name):
        raise HTTPException(404, "not found")
    return {"ok": True}


@router.post("/outcomes/compute")
def compute_outcomes(session: Session = Depends(get_session)):
    from instilens.services.outcomes import compute_outcomes as run

    return {"updated": run(session)}
