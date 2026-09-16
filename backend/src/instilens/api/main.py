from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from instilens import __version__
from instilens.api.hardening import (
    AuthRateLimitMiddleware,
    SecurityHeadersMiddleware,
    assert_production_ready,
)
from instilens.api.routes.admin import router as admin_router
from instilens.api.routes.auth import router as auth_router
from instilens.api.routes.public import router as public_router
from instilens.api.routes.userdata import router as userdata_router
from instilens.api.routes.v1 import router, ticket_router
from instilens.config import settings

_prod = settings.environment == "production"
app = FastAPI(
    title="InstiLens API",
    version=__version__,
    description="Smart-money & institutional intelligence. Data, not advice.",
    # Interactive docs are a dev convenience; in production they only enumerate the attack surface.
    docs_url=None if _prod else "/docs",
    redoc_url=None,
    openapi_url=None if _prod else "/openapi.json",
)
assert_production_ready()
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(AuthRateLimitMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_methods=["GET", "POST", "DELETE", "PATCH", "PUT"],
    allow_headers=["content-type", "authorization"],
)
app.include_router(auth_router)
app.include_router(router)
app.include_router(ticket_router)
app.include_router(userdata_router)
app.include_router(admin_router)
app.include_router(public_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}
