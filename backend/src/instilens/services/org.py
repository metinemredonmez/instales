"""Organisations: a team that shares its owner's plan (services/plans.org_plan).

One organisation per owner; a user belongs to at most one. The owner invites by e-mail: an `org_members` row with
`invited_email` and an ORG_INVITE token (auth_tokens, issued to the owner, `subject` = that row's id, 7 days,
single use) mailed as `{public_url}/org/accept?token=…` through the same out-of-band path as reset links. Whoever
opens the link must be signed in with that exact, verified address — the verified mailbox proves the address, the
address names the seat, the token names the row, so a link spends nothing but its own invitation — and becomes a
member (`user_id`, `accepted_at`). The invitation itself says nothing about the address (whether it has an account,
whether it sits in a team): the seat is reserved and the mail goes out either way; `accept` is where an account
already in an organisation is turned away. Seats count every row, the owner included; the cap is the owner's
plan's `org_seats` and, like every cap, applies only while plans are enforced.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.enums import AuthTokenKind, OrgRole
from instilens.domain.models import Organization, OrgMember, User
from instilens.services import auth, plans
from instilens.services.plans import FEATURES, PlanLimit, upgrade_for

INVITE_TTL = timedelta(days=7)
MAX_NAME = 64


class OrgError(Exception):
    pass


# ---------------------------------------------------------------- reads
def owned(session: Session, user: User) -> Organization | None:
    return session.scalar(select(Organization).where(Organization.owner_user_id == user.id))


def of_user(session: Session, user: User) -> Organization | None:
    """The organisation the user owns or has accepted membership of; None otherwise."""
    orgs = plans.memberships(session, user)
    return orgs[0] if orgs else None


def _member(session: Session, org: Organization, member_id: int) -> OrgMember | None:
    return session.scalar(select(OrgMember).where(OrgMember.id == member_id, OrgMember.org_id == org.id))


def seats_used(session: Session, org: Organization) -> int:
    return session.scalar(select(func.count(OrgMember.id)).where(OrgMember.org_id == org.id)) or 0


def payload(session: Session, org: Organization, viewer: User) -> dict:
    """The org block of /org and /billing: the live plan (mirror columns refreshed), seats, every member row."""
    plan = plans.refresh_org(session, org)
    rows = session.execute(
        select(OrgMember, User).outerjoin(User, User.id == OrgMember.user_id).where(OrgMember.org_id == org.id).order_by(OrgMember.id)
    ).all()
    return {
        "id": org.id, "name": org.name, "plan": plan, "seats": int(FEATURES["org_seats"][plan]), "seats_used": len(rows),
        "owner_user_id": org.owner_user_id, "is_owner": org.owner_user_id == viewer.id, "created_at": org.created_at.isoformat(),
        "members": [
            {"id": m.id, "role": m.role, "email": m.invited_email, "name": u.name if u else None, "user_id": m.user_id,
             "accepted_at": m.accepted_at.isoformat() if m.accepted_at else None, "pending": m.accepted_at is None, "invited_at": m.created_at.isoformat()}
            for m, u in rows
        ],
    }


# ---------------------------------------------------------------- mutations
def create(session: Session, user: User, name: str) -> Organization:
    """A new organisation owned by `user`, who is its first (accepted) member. Gated on the plan's seats: a plan
    with none cannot open one while plans are enforced."""
    name = " ".join(name.split())[:MAX_NAME]
    if len(name) < 2:
        raise OrgError("name must be at least 2 characters")
    if of_user(session, user) is not None:
        raise OrgError("you already belong to an organisation")
    if plans.enforced(user):
        plan = plans.effective_plan(session, user)[0]
        if not FEATURES["org_seats"][plan]:
            raise PlanLimit("org_seats", plan, 0, upgrade_for("org_seats", plan))
    org = Organization(name=name, owner_user_id=user.id)
    session.add(org)
    session.flush()
    plans.refresh_org(session, org)
    session.add(OrgMember(org_id=org.id, user_id=user.id, role=OrgRole.OWNER.value, invited_email=user.email, accepted_at=datetime.now(UTC)))
    session.flush()
    auth.audit(session, "org.created", actor=user.email, subject=org.name)
    return org


def rename(session: Session, org: Organization, actor: User, name: str) -> None:
    _owner_only(org, actor)
    name = " ".join(name.split())[:MAX_NAME]
    if len(name) < 2:
        raise OrgError("name must be at least 2 characters")
    org.name = name


def delete(session: Session, org: Organization, actor: User) -> None:
    """The owner closes the organisation: every member loses the inherited plan at once (memberships go with it)."""
    _owner_only(org, actor)
    for m in session.scalars(select(OrgMember).where(OrgMember.org_id == org.id)):
        session.delete(m)
    session.delete(org)
    session.flush()
    auth.audit(session, "org.deleted", actor=actor.email, subject=org.name)


def _owner_only(org: Organization, actor: User) -> None:
    if org.owner_user_id != actor.id:
        raise OrgError("only the owner can do this")


def invite(session: Session, org: Organization, actor: User, email: str, ip: str | None = None) -> tuple[OrgMember, bool]:
    """Add a pending seat for `email` and mail the link. Returns (member row, mail queued). Re-inviting a pending
    address mails a fresh link (the earlier one stays valid until one is used); an accepted member is refused."""
    _owner_only(org, actor)
    email = email.strip().lower()
    if not email or "@" not in email:
        raise OrgError("a valid e-mail address is required")
    existing = session.scalar(select(OrgMember).where(OrgMember.org_id == org.id, OrgMember.invited_email == email))
    if existing is not None and existing.accepted_at is not None:
        raise OrgError("already a member")
    invitee = session.scalar(select(User).where(User.email == email))  # only for the mail's language; never for the answer
    if existing is None:
        plan = plans.refresh_org(session, org)
        seats = int(FEATURES["org_seats"][plan])
        if plans.enforced(actor) and seats_used(session, org) >= seats:
            raise PlanLimit("org_seats", plan, seats, upgrade_for("org_seats", plan))
        existing = OrgMember(org_id=org.id, role=OrgRole.MEMBER.value, invited_email=email)
        session.add(existing)
        session.flush()
    token = auth.mint_token(session, actor, AuthTokenKind.ORG_INVITE, INVITE_TTL, subject=str(existing.id))
    link = f"{settings.public_url}/org/accept?token={token}"
    days = INVITE_TTL.days
    lang = invitee.lang if invitee is not None and invitee.lang in ("tr", "en") else (actor.lang if actor.lang in ("tr", "en") else "tr")
    if lang == "en":
        sent = _mail(email, f"InstiLens · invitation to {org.name}",
                     f"{actor.name} invited you to the organisation \"{org.name}\" on InstiLens.\n\nOpen this link while signed in as {email} to accept (valid {days} days, single use):\n\n{link}\n\nIf you do not have an account yet, register with this address first.")
    else:
        sent = _mail(email, f"InstiLens · {org.name} daveti",
                     f"{actor.name} seni InstiLens'te \"{org.name}\" organizasyonuna davet etti.\n\nKabul etmek için {email} hesabınla oturum açıkken bu bağlantıyı aç ({days} gün geçerli, tek kullanımlık):\n\n{link}\n\nHenüz hesabın yoksa önce bu adresle kayıt ol.")
    auth.audit(session, "org.invited", actor=actor.email, subject=email, ip=ip, detail=org.name)
    return existing, sent


def _mail(to: str, subject: str, body: str) -> bool:
    from instilens.services import notify

    return notify.queue_email(to, subject, body)


def accept(session: Session, user: User, token: str, ip: str | None = None) -> Organization:
    """Turn the invitation behind `token` into a membership for the signed-in account. The token names the owner
    (hence the organisation) and, in `subject`, the invitation row; the account's e-mail must be that row's — and
    verified, where the deployment can verify at all (SMTP), since an unverified account can be registered on any
    address by whoever holds the link. Burnt on success only."""
    from instilens.services.notify import smtp_configured

    row, owner = auth.peek_token(session, AuthTokenKind.ORG_INVITE, token)
    org = owned(session, owner)
    member = None
    if org is not None and row.subject and row.subject.isdigit():
        member = session.scalar(select(OrgMember).where(OrgMember.id == int(row.subject), OrgMember.org_id == org.id, OrgMember.invited_email == user.email, OrgMember.accepted_at.is_(None)))
    if member is None:
        raise auth.AuthError("invalid or expired link")
    if not user.email_verified and smtp_configured():
        raise OrgError("verify your e-mail address first")
    if of_user(session, user) is not None:
        raise OrgError("you already belong to an organisation")
    row.used_at = datetime.now(UTC)
    member.user_id, member.accepted_at = user.id, datetime.now(UTC)
    session.flush()
    auth.audit(session, "org.joined", actor=user.email, subject=org.name, ip=ip)
    return org


def remove(session: Session, org: Organization, actor: User, member_id: int) -> None:
    """The owner drops a member or a pending invitation; a member may drop their own seat. Never the owner's row."""
    member = _member(session, org, member_id)
    if member is None:
        raise OrgError("not found")
    if member.role == OrgRole.OWNER.value:
        raise OrgError("the owner's seat cannot be removed; delete the organisation instead")
    if org.owner_user_id != actor.id and member.user_id != actor.id:
        raise OrgError("only the owner can remove other members")
    session.delete(member)
    session.flush()
    auth.audit(session, "org.removed", actor=actor.email, subject=member.invited_email, detail=org.name)
