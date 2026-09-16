"""Security middleware and helpers: response headers, layered rate limits, client-IP resolution.

Design (mapped to the ASVS 5.0 checklist we audit against):
- Client IP comes from X-Forwarded-For ONLY when the direct peer is a trusted proxy (nginx on loopback);
  otherwise the socket address is used, so the header cannot be spoofed to dodge limits (CWE-290).
- Limits are layered: per-IP, per-account (login e-mail), per-user (paid AI endpoints) and a global
  capacity for auth, so a distributed attacker or a single noisy account both hit a wall (ASVS V13/7.17).
- Counters live in the database (`rate_hits`) so every uvicorn worker sees the same numbers; the same table
  backs the per-account lockout. `pipeline_runs` doubles as the cross-process lock for the admin pipeline run.
  Before the first migration (no table yet) the counters fall back to process memory with a warning; any OTHER
  database error fails CLOSED (deny / report the key as locked) after a short retry, because a limiter that
  cannot count must never silently allow — a transient error used to make an active lockout evaporate.
"""

from __future__ import annotations

import ipaddress
import logging
import time
from collections import defaultdict, deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError, OperationalError, ProgrammingError
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from instilens.config import settings
from instilens.domain.enums import PipelineStatus
from instilens.domain.models import PipelineRun, RateHit

log = logging.getLogger("instilens.hardening")

# path → (per-IP per minute, global per minute)
RATE_LIMITED_PATHS: dict[str, tuple[int | None, int]] = {  # None = settings.auth_rate_limit_per_minute (admin-editable at runtime)
    "/api/v1/auth/login": (None, 300),
    "/api/v1/auth/register": (None, 100),
    "/api/v1/auth/password": (5, 100),
    "/api/v1/auth/ticket": (60, 3000),
    "/api/v1/auth/forgot": (5, 100),
    "/api/v1/auth/reset": (5, 100),
    "/api/v1/auth/verify": (10, 200),
    "/api/v1/auth/verify/resend": (3, 100),
    "/api/v1/auth/mfa/verify": (10, 300),
    "/api/v1/public/waitlist": (5, 200),
    "/api/v1/me/settings/test": (3, 60),
    "/api/v1/alerts/evaluate": (3, 60),
    "/api/v1/push/test": (3, 60),
}

# ---------------------------------------------------------------- shared counter store (database)
_engine: Engine | None = None  # bound explicitly by tests; production uses the app engine lazily
_MEMORY: dict[str, deque[float]] = defaultdict(deque)  # fallback only while the table does not exist yet
_MAX_WINDOW_S = 24 * 3600  # nothing counts longer than a day; older rows are swept
_SWEEP_EVERY_S = 600.0
_last_sweep = 0.0
_DB_ERRORS = (OperationalError, ProgrammingError)
_RETRIES = 3  # SQLite "database is locked" under two workers is usually gone within a few milliseconds
_RETRY_SLEEP_S = 0.05
LOCKED_OUT = 1 << 30  # what failures() reports when the store cannot be read: above every configured threshold


def use_engine(engine: Engine | None) -> None:
    """Point the counter store at an engine (tests). None = the application engine from db.session."""
    global _engine
    _engine = engine


def _store() -> Session:
    if _engine is None:
        from instilens.db.session import get_engine

        return Session(bind=get_engine(), expire_on_commit=False)
    return Session(bind=_engine, expire_on_commit=False)


def _no_table(exc: Exception) -> bool:
    """True only for "rate_hits does not exist yet" — the one error we may answer from process memory."""
    msg = str(getattr(exc, "orig", exc)).lower()
    return "no such table" in msg or "does not exist" in msg or "doesn't exist" in msg or "undefinedtable" in msg


def _run(work, *, before_migrations, on_failure):
    """Run `work(session)` against the counter store.

    Missing table → `before_migrations()` (process memory, bootstrap only). Any other database error is retried
    briefly and then answered with `on_failure()`, which every caller makes the RESTRICTIVE answer. The warning is
    logged every time on purpose: a silently degrading limiter that warns once per worker is invisible in the logs.
    """
    last: Exception | None = None
    for attempt in range(_RETRIES):
        try:
            with _store() as s:
                return work(s)
        except _DB_ERRORS as exc:
            last = exc
            if _no_table(exc):
                log.warning("rate_hits table unavailable (%s); counters are per-process until migrations run", exc)
                return before_migrations()
            if attempt + 1 < _RETRIES:
                time.sleep(_RETRY_SLEEP_S * (attempt + 1))
    log.error("counter store unreachable (%s); failing closed", last)
    return on_failure()


def _sweep(s: Session, now: datetime) -> None:
    """Drop rows no window can still see; at most once per _SWEEP_EVERY_S per process."""
    global _last_sweep
    mono = time.monotonic()
    if mono - _last_sweep < _SWEEP_EVERY_S:
        return
    _last_sweep = mono
    s.execute(delete(RateHit).where(RateHit.ts < now - timedelta(seconds=_MAX_WINDOW_S)))


def _mem_hit(key: str, limit: int, window_s: float) -> bool:
    now = time.monotonic()
    q = _MEMORY[key]
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


def _mem_failures(key: str, window_s: float) -> int:
    now = time.monotonic()
    q = _MEMORY[key]
    while q and now - q[0] > window_s:
        q.popleft()
    return len(q)


def reset_rate_limits() -> None:
    """Clear every counter (tests, `instilens db reset`): the table and the in-memory fallback."""
    _MEMORY.clear()

    def _do(s: Session) -> None:
        s.execute(delete(RateHit))
        s.commit()

    _run(_do, before_migrations=lambda: None, on_failure=lambda: None)  # best effort: clearing cannot be unsafe


def _trusted(peer: str | None) -> bool:
    if not peer:
        return False
    try:
        ip = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return any(ip in ipaddress.ip_network(n, strict=False) for n in settings.trusted_proxies)


def client_ip(request: Request) -> str:
    """Real client address: the socket peer, or the LAST X-Forwarded-For hop when the peer is our proxy."""
    peer = request.client.host if request.client else None
    if _trusted(peer):
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[-1].strip()  # nginx appends the true client last
    return peer or "?"


def hit(key: str, limit: int, window_s: float = 60.0) -> bool:
    """Record one hit for `key`; True if still within `limit` per `window_s`.

    The counter is shared across workers, but the read-then-insert is NOT atomic: two workers can both see
    `n < limit` and both insert, so the effective ceiling is `limit` plus at most one hit per concurrent worker.
    That slop is accepted — these are abuse brakes, not quotas. An unreachable store returns False (deny).
    """
    now = datetime.now(UTC)
    cutoff = now - timedelta(seconds=window_s)

    def _do(s: Session) -> bool:
        s.execute(delete(RateHit).where(RateHit.key == key, RateHit.ts < cutoff))
        _sweep(s, now)
        n = s.scalar(select(func.count()).select_from(RateHit).where(RateHit.key == key, RateHit.ts >= cutoff)) or 0
        if n >= limit:
            s.commit()
            return False
        s.add(RateHit(key=key, ts=now))
        s.commit()
        return True

    return _run(_do, before_migrations=lambda: _mem_hit(key, limit, window_s), on_failure=lambda: False)


def failures(key: str, window_s: float = 60.0) -> int:
    """How many hits `key` has inside the window (without recording one).

    This is the lockout READ path: when the store cannot be reached it reports LOCKED_OUT, so an unverifiable
    lockout stays a lockout instead of evaporating into a fresh set of password guesses.
    """
    cutoff = datetime.now(UTC) - timedelta(seconds=window_s)

    def _do(s: Session) -> int:
        return s.scalar(select(func.count()).select_from(RateHit).where(RateHit.key == key, RateHit.ts >= cutoff)) or 0

    return _run(_do, before_migrations=lambda: _mem_failures(key, window_s), on_failure=lambda: LOCKED_OUT)


def reset_key(key: str) -> None:
    _MEMORY.pop(key, None)

    def _do(s: Session) -> None:
        s.execute(delete(RateHit).where(RateHit.key == key))
        s.commit()

    _run(_do, before_migrations=lambda: None, on_failure=lambda: None)  # best effort: clearing cannot be unsafe


# ---------------------------------------------------------------- pipeline run lock (database)
PIPELINE_LOCK = "run"
PIPELINE_STALE_AFTER = timedelta(hours=3)  # a run this old with no finish is a crashed worker: take the lock over


def _run_dict(run: PipelineRun | None) -> dict:
    if run is None:
        return {"running": False, "started_at": None, "finished_at": None, "result": None, "error": None}
    return {"running": run.status == PipelineStatus.RUNNING, "started_at": run.started_at.isoformat() if run.started_at else None,
            "finished_at": run.finished_at.isoformat() if run.finished_at else None, "result": run.result, "error": run.error,
            "started_by": run.started_by}


def acquire_pipeline_lock(started_by: str | None = None) -> int | None:
    """Insert the RUNNING row that holds the unique `lock_key`; None when another run holds it."""
    now = datetime.now(UTC)
    with _store() as s:
        stale = s.scalar(select(PipelineRun).where(PipelineRun.lock_key == PIPELINE_LOCK, PipelineRun.started_at < now - PIPELINE_STALE_AFTER))
        if stale is not None:
            stale.lock_key, stale.status, stale.finished_at, stale.error = None, PipelineStatus.ABANDONED, now, "worker did not report back"
            s.commit()
        run = PipelineRun(lock_key=PIPELINE_LOCK, status=PipelineStatus.RUNNING, started_by=started_by, started_at=now)
        s.add(run)
        try:
            s.commit()
        except IntegrityError:
            s.rollback()
            return None
        return run.id


def finish_pipeline_run(run_id: int, *, result: dict | None = None, error: str | None = None) -> None:
    with _store() as s:
        run = s.get(PipelineRun, run_id)
        if run is None:
            return
        run.lock_key, run.finished_at = None, datetime.now(UTC)
        run.status, run.result, run.error = (PipelineStatus.ERROR if error else PipelineStatus.OK), result, error
        s.commit()


def pipeline_run_status() -> dict:
    """The run in progress, else the most recent one — same shape the admin UI already reads."""
    with _store() as s:
        run = s.scalar(select(PipelineRun).where(PipelineRun.lock_key == PIPELINE_LOCK)) or s.scalar(select(PipelineRun).order_by(PipelineRun.id.desc()).limit(1))
        return _run_dict(run)


def too_many(retry_after: int = 60) -> JSONResponse:
    return JSONResponse({"detail": "too many attempts, try again later"}, status_code=429, headers={"Retry-After": str(retry_after)})


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        h = response.headers
        h.setdefault("X-Content-Type-Options", "nosniff")
        h.setdefault("X-Frame-Options", "DENY")
        h.setdefault("Referrer-Policy", "no-referrer")
        h.setdefault("Cache-Control", "no-store")
        h.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")
        h.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        h.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        if settings.environment == "production":
            h.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-IP + global caps on the endpoints an anonymous or cheap client could hammer."""

    async def dispatch(self, request: Request, call_next) -> Response:
        cfg = RATE_LIMITED_PATHS.get(request.url.path) if request.method == "POST" else None
        if cfg:
            per_ip, global_cap = cfg
            per_ip = per_ip or settings.auth_rate_limit_per_minute
            # counters are database rows now: keep the event loop free while they are updated
            ok = await run_in_threadpool(hit, f"ip:{client_ip(request)}:{request.url.path}", per_ip)
            if ok:
                ok = await run_in_threadpool(hit, f"global:{request.url.path}", global_cap)
            if not ok:
                return too_many()
        return await call_next(request)


def assert_production_ready() -> None:
    if settings.environment != "production":
        return
    if settings.jwt_secret == "dev-only-change-me" or len(settings.jwt_secret) < 32:
        raise RuntimeError("INSTILENS_JWT_SECRET must be set to at least 32 random characters in production")
