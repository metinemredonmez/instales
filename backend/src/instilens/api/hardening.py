"""Security middleware and helpers: response headers, layered rate limits, client-IP resolution.

Design (mapped to the ASVS 5.0 checklist we audit against):
- Client IP comes from X-Forwarded-For ONLY when the direct peer is a trusted proxy (nginx on loopback);
  otherwise the socket address is used, so the header cannot be spoofed to dodge limits (CWE-290).
- Limits are layered: per-IP, per-account (login e-mail), per-user (paid AI endpoints) and a global
  capacity for auth, so a distributed attacker or a single noisy account both hit a wall (ASVS V13/7.17).
- The store is in-memory and per-process; behind several replicas move it to Redis (same interface).
"""

from __future__ import annotations

import ipaddress
import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from instilens.config import settings

# path → (per-IP per minute, global per minute)
RATE_LIMITED_PATHS: dict[str, tuple[int, int]] = {
    "/api/v1/auth/login": (settings.auth_rate_limit_per_minute, 300),
    "/api/v1/auth/register": (settings.auth_rate_limit_per_minute, 100),
    "/api/v1/auth/password": (5, 100),
    "/api/v1/auth/ticket": (60, 3000),
    "/api/v1/public/waitlist": (5, 200),
    "/api/v1/me/settings/test": (3, 60),
    "/api/v1/alerts/evaluate": (3, 60),
}
_HITS: dict[str, deque[float]] = defaultdict(deque)


def reset_rate_limits() -> None:
    _HITS.clear()


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
    """Record one hit for `key`; True if still within `limit` per `window_s`."""
    now = time.monotonic()
    q = _HITS[key]
    while q and now - q[0] > window_s:
        q.popleft()
    if len(q) >= limit:
        return False
    q.append(now)
    return True


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
            if not hit(f"ip:{client_ip(request)}:{request.url.path}", per_ip) or not hit(f"global:{request.url.path}", global_cap):
                return too_many()
        return await call_next(request)


def assert_production_ready() -> None:
    if settings.environment != "production":
        return
    if settings.jwt_secret == "dev-only-change-me" or len(settings.jwt_secret) < 32:
        raise RuntimeError("INSTILENS_JWT_SECRET must be set to at least 32 random characters in production")
