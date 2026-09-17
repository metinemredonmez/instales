from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from instilens import __version__
from instilens.ai.assess import AiUnavailable
from instilens.api.hardening import (
    AuthRateLimitMiddleware,
    SecurityHeadersMiddleware,
    assert_production_ready,
)
from instilens.api.routes.admin import router as admin_router
from instilens.api.routes.auth import router as auth_router
from instilens.api.routes.billing import router as billing_router
from instilens.api.routes.billing import webhook_router as billing_webhook_router
from instilens.api.routes.org import router as org_router
from instilens.api.routes.portfolio import router as portfolio_router
from instilens.api.routes.public import router as public_router
from instilens.api.routes.userdata import router as userdata_router
from instilens.api.routes.v1 import router, ticket_router
from instilens.config import settings
from instilens.services.plans import PlanLimit

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
app.include_router(portfolio_router)
app.include_router(org_router)
app.include_router(billing_router)
app.include_router(billing_webhook_router)
app.include_router(admin_router)
app.include_router(public_router)


@app.exception_handler(PlanLimit)
def _plan_limit(_request: Request, exc: PlanLimit) -> JSONResponse:
    """A gated feature or an exhausted cap (services/plans), wherever it was raised — a route dependency or a
    service — answers 402 with one shape the SPA turns into the upgrade prompt: {"detail": "plan_limit", "feature",
    "plan", "limit", "upgrade"}."""
    return JSONResponse(exc.detail(), status_code=402)


@app.exception_handler(AiUnavailable)
def _ai_unavailable(_request: Request, exc: AiUnavailable) -> JSONResponse:
    """Any route that reaches the model (notes, briefs, news tagging) answers 503 when the model side fails.
    Importing the class here is cheap: ai/assess pulls the anthropic SDK in lazily, inside _client()."""
    return JSONResponse({"detail": f"ai unavailable: {exc}"}, status_code=503, headers={"Retry-After": "60"})


@app.on_event("startup")
def _apply_runtime_settings() -> None:
    """Admin overrides from the DB win over .env for the editable keys (see services/runtime_settings)."""
    from instilens.db.session import session_scope
    from instilens.services import runtime_settings

    try:
        with session_scope() as s:
            runtime_settings.apply(s)
    except Exception as exc:  # noqa: BLE001 — a missing table on first boot must not stop the API
        import logging

        logging.getLogger("instilens.api").warning("runtime settings not applied: %s", exc)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}
