"""Desktop release store — pure rules + file handling.

Mirrors the approach proven in Emre's other product: the SERVER hands out version numbers (one source of
truth, so a Mac build and a Linux/Windows build land in the same release), files are classified from their
names, a release is PUBLISHED explicitly, and the updater endpoint only ever sees published releases.
Files live on local disk under `settings.releases_dir/<version>/`.
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from instilens.config import settings
from instilens.domain.models import Release, ReleaseFile

SEMVER = re.compile(r"^(\d{1,6})\.(\d{1,6})\.(\d{1,6})$")
PLATFORMS = ("darwin-aarch64", "darwin-x86_64", "windows-x86_64", "linux-x86_64")
PLATFORM_LABEL = {"darwin-aarch64": "macOS · Apple Silicon", "darwin-x86_64": "macOS · Intel", "windows-x86_64": "Windows", "linux-x86_64": "Linux"}
SAFE_NAME = re.compile(r"^[A-Za-z0-9._+-]{3,200}$")


class ReleaseError(ValueError):
    pass


def parse(v: str) -> tuple[int, int, int] | None:
    m = SEMVER.match(str(v or "").strip())
    return (int(m[1]), int(m[2]), int(m[3])) if m else None


def compare(a: str, b: str) -> int | None:
    x, y = parse(a), parse(b)
    if x is None or y is None:
        return None
    return (x > y) - (x < y)


def bump(v: str) -> str:
    p = parse(v)
    if p is None:
        raise ReleaseError(f"bad version {v!r}")
    return f"{p[0]}.{p[1]}.{p[2] + 1}"


def classify(filename: str) -> tuple[str, str]:
    """(platform, kind) from a Tauri artifact name; raises for anything we don't ship."""
    n = filename.lower()
    arm = "aarch64" in n or "arm64" in n
    if n.endswith(".app.tar.gz"):
        return ("darwin-aarch64" if arm else "darwin-x86_64", "UPDATE")
    if n.endswith(".dmg"):
        return ("darwin-aarch64" if arm else "darwin-x86_64", "INSTALLER")
    if n.endswith("-setup.exe") or n.endswith(".msi"):
        return ("windows-x86_64", "UPDATE" if n.endswith("-setup.exe") else "INSTALLER")
    if n.endswith(".exe"):
        return ("windows-x86_64", "INSTALLER")
    if n.endswith(".appimage"):
        return ("linux-x86_64", "UPDATE")
    if n.endswith(".deb") or n.endswith(".rpm"):
        return ("linux-x86_64", "INSTALLER")
    raise ReleaseError(f"unsupported artifact {filename!r}")


def latest_version(session: Session, published_only: bool = False) -> str | None:
    stmt = select(Release.version)
    if published_only:
        stmt = stmt.where(Release.status == "PUBLISHED")
    versions = [v for v in session.scalars(stmt) if parse(v)]
    return max(versions, key=parse) if versions else None


def next_version(session: Session, actor: str | None) -> Release:
    """Reuse the open draft if there is one (so a second machine adds to it); else create the next number."""
    draft = session.scalar(select(Release).where(Release.status == "DRAFT").order_by(Release.created_at.desc()))
    if draft:
        return draft
    last = latest_version(session)
    rel = Release(version=bump(last) if last else "0.2.0", status="DRAFT", created_by=actor)
    session.add(rel)
    session.flush()
    return rel


def get_or_create(session: Session, version: str, actor: str | None) -> Release:
    if parse(version) is None:
        raise ReleaseError("version must be major.minor.patch")
    rel = session.scalar(select(Release).where(Release.version == version))
    if rel:
        if rel.status == "WITHDRAWN":
            raise ReleaseError(f"{version} was withdrawn; use a new version")
        return rel  # DRAFT or PUBLISHED: a platform built later still joins the same version
    last = latest_version(session)
    if last and (compare(version, last) or 0) < 0:
        raise ReleaseError(f"version cannot go backwards (latest is {last})")
    rel = Release(version=version, status="DRAFT", created_by=actor)
    session.add(rel)
    session.flush()
    return rel


def store_file(session: Session, rel: Release, filename: str, data: bytes, signature: str | None) -> ReleaseFile:
    if not SAFE_NAME.match(filename) or ".." in filename:
        raise ReleaseError("unsafe filename")
    platform, kind = classify(filename)
    if kind == "UPDATE" and not signature:
        raise ReleaseError(f"{filename}: updater artifacts need their .sig signature")
    folder = Path(settings.releases_dir) / rel.version
    folder.mkdir(parents=True, exist_ok=True)
    (folder / filename).write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    row = session.scalar(select(ReleaseFile).where(ReleaseFile.release_id == rel.id, ReleaseFile.filename == filename))
    if row is None:
        row = ReleaseFile(release_id=rel.id, platform=platform, kind=kind, filename=filename, size=len(data), sha256=digest, signature=signature)
        session.add(row)
    else:
        row.platform, row.kind, row.size, row.sha256, row.signature, row.uploaded_at = platform, kind, len(data), digest, signature, datetime.now(UTC)
    session.flush()
    # One artifact per (platform, kind, extension): a re-upload or a build from another machine replaces the older one,
    # so a draft that collected several builds never lists stale files (e.g. 0.2.0 leftovers inside 0.2.2).
    ext = _ext(filename)
    for old in list(rel.files):
        if old.id != row.id and old.platform == platform and old.kind == kind and _ext(old.filename) == ext:
            (folder / old.filename).unlink(missing_ok=True)
            session.delete(old)
    session.flush()
    return row


def _ext(name: str) -> str:
    n = name.lower()
    for e in (".app.tar.gz", "-setup.exe", ".appimage", ".dmg", ".msi", ".exe", ".deb", ".rpm"):
        if n.endswith(e):
            return e
    return n.rsplit(".", 1)[-1]


def downloadable(f: ReleaseFile, siblings: list[ReleaseFile]) -> bool:
    """What humans download: INSTALLER files, plus a Windows -setup.exe (it is the installer AND the updater bundle)
    when the release has no separate .msi."""
    if f.kind == "INSTALLER":
        return True
    return f.platform == "windows-x86_64" and f.filename.lower().endswith("-setup.exe") and not any(
        x.platform == f.platform and x.kind == "INSTALLER" for x in siblings)


def file_path(row: ReleaseFile) -> Path:
    return Path(settings.releases_dir) / row.release.version / row.filename


def set_status(session: Session, rel: Release, status: str) -> None:
    if status not in ("DRAFT", "PUBLISHED", "WITHDRAWN"):
        raise ReleaseError("bad status")
    if status == "PUBLISHED":
        if not any(downloadable(f, rel.files) for f in rel.files):
            raise ReleaseError("nothing to publish: no installer uploaded yet")
        rel.published_at = datetime.now(UTC)
    rel.status = status


def release_json(rel: Release, base_url: str) -> dict:
    return {
        "id": rel.id, "version": rel.version, "status": rel.status, "notes": rel.notes, "created_by": rel.created_by,
        "created_at": rel.created_at.isoformat(), "published_at": rel.published_at.isoformat() if rel.published_at else None,
        "files": [{"id": f.id, "platform": f.platform, "label": PLATFORM_LABEL.get(f.platform, f.platform), "kind": f.kind, "filename": f.filename,
                   "size": f.size, "sha256": f.sha256, "signed": bool(f.signature), "downloads": f.downloads, "downloadable": downloadable(f, rel.files),
                   "url": f"{base_url}/api/v1/public/desktop/download/{f.id}/{f.filename}"} for f in sorted(rel.files, key=lambda x: (x.platform, x.kind, x.filename))],
    }


def latest_published(session: Session) -> Release | None:
    v = latest_version(session, published_only=True)
    return session.scalar(select(Release).where(Release.version == v)) if v else None


def updater_manifest(session: Session, target: str, arch: str, current: str, base_url: str) -> dict | None:
    """Tauri updater dynamic response: None (→ 204) when up to date; else the platform's UPDATE artifact."""
    rel = latest_published(session)
    if rel is None or (compare(rel.version, current) or 0) <= 0:
        return None
    key = f"{target}-{arch}"
    f = next((x for x in rel.files if x.platform == key and x.kind == "UPDATE" and x.signature), None)
    if f is None:
        return None
    return {"version": rel.version, "notes": rel.notes, "pub_date": (rel.published_at or rel.created_at).replace(tzinfo=UTC).isoformat(),
            "platforms": {key: {"signature": f.signature, "url": f"{base_url}/api/v1/public/desktop/download/{f.id}/{f.filename}"}}}
