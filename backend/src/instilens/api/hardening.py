"""Security middleware: response headers and a small in-memory rate limiter for auth endpoints.

The limiter is per-process; behind several API replicas move it to Redis (same interface).
"""

from __future__ import annotations

import time
from collections import defaultdict, deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from instilens.config import settings

RATE_LIMITED_PATHS = ("/api/v1/auth/login", "/api/v1/auth/register", "/api/v1/public/waitlist")
_HITS: dict[str, deque[float]] = defaultdict(deque)


def reset_rate_limits() -> None:
    _HITS.clear()


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cache-Control", "no-store")
        if settings.environment == "production":
            response.headers.setdefault("Strict-Transport-Security", "max-age=63072000; includeSubDomains")
        return response


class AuthRateLimitMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, per_minute: int | None = None) -> None:
        super().__init__(app)
        self.per_minute = per_minute or settings.auth_rate_limit_per_minute
        self.hits = _HITS

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.method == "POST" and request.url.path in RATE_LIMITED_PATHS:
            ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
            now = time.monotonic()
            q = self.hits[ip]
            while q and now - q[0] > 60:
                q.popleft()
            if len(q) >= self.per_minute:
                return JSONResponse({"detail": "too many attempts, try again in a minute"}, status_code=429, headers={"Retry-After": "60"})
            q.append(now)
        return await call_next(request)


def assert_production_ready() -> None:
    if settings.environment == "production" and settings.jwt_secret == "dev-only-change-me":
        raise RuntimeError("INSTILENS_JWT_SECRET must be set in production")
