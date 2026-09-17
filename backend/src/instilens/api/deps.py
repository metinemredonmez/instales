from collections.abc import Iterator

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from instilens.db.session import session_scope
from instilens.domain.models import User
from instilens.services import auth


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def _bearer(request: Request) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return None


def current_user(session: Session = Depends(get_session), token: str | None = Depends(_bearer)) -> User:
    if not token:
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        return auth.user_from_token(session, token)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc


def ticket_user(session: Session = Depends(get_session), ticket: str | None = Query(None, include_in_schema=False), token: str | None = Depends(_bearer)) -> User:
    """For EventSource / <audio>, which cannot set headers: accepts a 5-minute ticket in the query string
    (or a normal bearer header). Session tokens are refused in the query so they never land in access logs."""
    try:
        if token:
            return auth.user_from_token(session, token)
        if ticket:
            return auth.user_from_token(session, ticket, expect_scope="ticket")
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc)) from exc
    raise HTTPException(401, "ticket required")


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(403, "admin only")
    return user


def require_plan(feature: str, *, ticket: bool = False):
    """A dependency that gates a route on a plan feature (services/plans.FEATURES): the signed-in user when their
    plan includes it, otherwise PlanLimit → 402 {"detail": "plan_limit", ...} (api/main). A no-op while plans are
    not enforced and for ADMIN. `ticket=True` for the query-string-authenticated routes (EventSource, <audio>)."""
    from instilens.services import plans

    def dep(session: Session = Depends(get_session), user: User = Depends(ticket_user if ticket else current_user)) -> User:
        plans.require(session, user, feature)
        return user

    return dep
