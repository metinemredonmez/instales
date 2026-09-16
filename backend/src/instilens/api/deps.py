from collections.abc import Iterator

from fastapi import Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from instilens.db.session import session_scope
from instilens.domain.models import User
from instilens.services import auth


def get_session() -> Iterator[Session]:
    with session_scope() as session:
        yield session


def _bearer(request: Request, token: str | None = Query(None, include_in_schema=False)) -> str | None:
    header = request.headers.get("authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return token  # EventSource cannot set headers, so the SSE stream accepts ?token=


def current_user(session: Session = Depends(get_session), token: str | None = Depends(_bearer)) -> User:
    if not token:
        raise HTTPException(401, "authentication required", headers={"WWW-Authenticate": "Bearer"})
    try:
        return auth.user_from_token(session, token)
    except auth.AuthError as exc:
        raise HTTPException(401, str(exc), headers={"WWW-Authenticate": "Bearer"}) from exc
