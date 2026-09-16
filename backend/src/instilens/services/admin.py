"""Admin: user management and entity review (auto-created, unverified instruments/funds/institutions)."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.domain.models import Disclosure, Fund, Institution, Instrument, User


def list_users(session: Session) -> list[dict]:
    return [
        {"id": u.id, "email": u.email, "name": u.name, "plan": u.plan, "role": u.role, "is_active": u.is_active,
         "created_at": u.created_at.isoformat(), "last_login_at": u.last_login_at.isoformat() if u.last_login_at else None}
        for u in session.scalars(select(User).order_by(User.id))
    ]


def update_user(session: Session, user_id: int, *, plan: str | None = None, role: str | None = None, is_active: bool | None = None) -> dict | None:
    u = session.get(User, user_id)
    if u is None:
        return None
    if plan is not None:
        u.plan = plan
    if role is not None:
        u.role = role
    if is_active is not None:
        u.is_active = is_active
    return {"id": u.id, "plan": u.plan, "role": u.role, "is_active": u.is_active}


def unverified(session: Session) -> dict:
    return {
        "instruments": [{"id": i.id, "market": i.market_code, "symbol": i.symbol, "name": i.name} for i in session.scalars(select(Instrument).where(Instrument.is_verified.is_(False)).order_by(Instrument.symbol))],
        "funds": [{"id": f.id, "code": f.code, "name": f.name, "institution": f.institution.name} for f in session.scalars(select(Fund).where(Fund.is_verified.is_(False)).order_by(Fund.code))],
        "institutions": [{"id": i.id, "market": i.market_code, "code": i.code, "name": i.name} for i in session.scalars(select(Institution).where(Institution.is_verified.is_(False)).order_by(Institution.name))],
        "failed_disclosures": [{"id": d.id, "source": d.source, "source_id": d.source_id, "kind": d.kind, "error": d.parse_error} for d in session.scalars(select(Disclosure).where(Disclosure.parse_status == "FAILED").order_by(Disclosure.id.desc()).limit(50))],
    }


def verify(session: Session, kind: str, entity_id: int, name: str | None = None) -> bool:
    model = {"instrument": Instrument, "fund": Fund, "institution": Institution}.get(kind)
    if model is None:
        return False
    row = session.get(model, entity_id)
    if row is None:
        return False
    row.is_verified = True
    if name:
        row.name = name.strip()
    return True
