"""Unauthenticated endpoints used by the public landing page (instilens.com)."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Response, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.api.deps import get_session
from instilens.config import settings
from instilens.domain.models import ReleaseFile, WaitlistEntry
from instilens.services import releases

router = APIRouter(prefix="/api/v1/public", tags=["public"])


class WaitlistBody(BaseModel):
    email: EmailStr
    name: str | None = Field(None, max_length=128)
    lang: str = Field("tr", pattern="^(tr|en)$")
    source: str | None = Field(None, max_length=64)


@router.post("/waitlist", status_code=201)
def join_waitlist(body: WaitlistBody, session: Session = Depends(get_session)):
    """Early-access signup. Idempotent per e-mail; rate-limited by the auth limiter."""
    email = body.email.lower().strip()
    existing = session.scalar(select(WaitlistEntry).where(WaitlistEntry.email == email))
    if existing:
        return {"ok": True, "already": True}
    session.add(WaitlistEntry(email=email, name=(body.name or "").strip() or None, lang=body.lang, source=body.source))
    return {"ok": True, "already": False}


# ---------------------------------------------------------------- desktop releases (public + CI)
def _ci_key(x_release_key: str | None = Header(None)) -> str:
    if not settings.release_upload_key or not x_release_key or not secrets.compare_digest(x_release_key, settings.release_upload_key):
        raise HTTPException(401, "release key required")
    return x_release_key


@router.get("/desktop/latest")
def desktop_latest(session: Session = Depends(get_session)):
    """Latest PUBLISHED release with installer links — for the landing page and the in-app download page."""
    rel = releases.latest_published(session)
    return releases.release_json(rel, settings.public_url) if rel else None


@router.get("/desktop/update/{target}/{arch}/{current}")
def desktop_update(target: str, arch: str, current: str, session: Session = Depends(get_session)):
    """Tauri updater endpoint. 204 = nothing newer; JSON = signed update for this platform."""
    m = releases.updater_manifest(session, target, arch, current, settings.public_url)
    if m is None:
        return Response(status_code=204)
    return m


@router.get("/desktop/download/{file_id}/{filename}")
def desktop_download(file_id: int, filename: str, session: Session = Depends(get_session)):
    f = session.get(ReleaseFile, file_id)
    if f is None or f.filename != filename or f.release.status != "PUBLISHED":
        raise HTTPException(404, "not found")
    path = releases.file_path(f)
    if not path.exists():
        raise HTTPException(404, "file missing on disk")
    f.downloads += 1
    return FileResponse(path, filename=f.filename, media_type="application/octet-stream")


@router.post("/desktop/ci/next", dependencies=[Depends(_ci_key)])
def desktop_ci_next(session: Session = Depends(get_session)):
    """Version number for a build. Reuses the open draft so builds from different machines merge."""
    rel = releases.next_version(session, actor="ci")
    return {"version": rel.version, "status": rel.status}


@router.post("/desktop/ci/upload", dependencies=[Depends(_ci_key)])
async def desktop_ci_upload(version: str = Form(...), file: UploadFile = File(...), signature: str | None = Form(None), notes: str | None = Form(None), session: Session = Depends(get_session)):
    """Upload one artifact into the draft for `version` (created if needed). Updater bundles need `signature`."""
    data = await file.read()
    if len(data) > 400 * 1024 * 1024:
        raise HTTPException(413, "artifact too large")
    try:
        rel = releases.get_or_create(session, version, actor="ci")
        if notes and not rel.notes:
            rel.notes = notes[:4000]
        row = releases.store_file(session, rel, file.filename or "", data, signature)
    except releases.ReleaseError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"version": rel.version, "file": row.filename, "platform": row.platform, "kind": row.kind, "size": row.size}
