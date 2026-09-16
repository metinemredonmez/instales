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
from instilens.api.routes.userdata import router as userdata_router
from instilens.api.routes.v1 import router
from instilens.config import settings

app = FastAPI(
    title="InstiLens API",
    version=__version__,
    description="Smart-money & institutional intelligence. Data, not advice.",
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
app.include_router(userdata_router)
app.include_router(admin_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}
