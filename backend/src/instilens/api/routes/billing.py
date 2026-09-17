"""Billing (services/billing): plans, checkout, the customer portal, the provider webhook, the account's standing.

GET  /billing/plans              the feature matrix with each paid tier's price as the provider states it; `configured`
POST /billing/checkout {plan}    {url} of a hosted Checkout Session (mode=subscription); 503 "payments not configured" until keys exist,
                                 409 "subscription_exists" while a paid subscription is live (change it through the portal instead)
POST /billing/portal {flow?}     {url} of the provider's Billing Portal; `flow: "subscription_update"` opens the plan picker of the live
                                 subscription; 503 unconfigured, 404 when the user never checked out
POST /billing/webhook            the provider's events — no auth, raw body, signature verified; 200 applied / duplicate / ignored, 400 bad signature
GET  /billing/me                 effective plan and its source (own | org | manual), the subscription behind it, the organisation
"""

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from instilens.api.deps import current_user, get_session
from instilens.api.hardening import client_ip
from instilens.config import settings
from instilens.domain.models import User
from instilens.services import billing
from instilens.services.auth import audit

router = APIRouter(prefix="/api/v1/billing", tags=["billing"], dependencies=[Depends(current_user)])
webhook_router = APIRouter(prefix="/api/v1/billing", tags=["billing"])  # the provider calls it: no session, signed body instead


class CheckoutBody(BaseModel):
    plan: str = Field(pattern="^(PRO|PRO_PLUS)$")


class PortalBody(BaseModel):
    flow: str | None = Field(None, pattern="^(subscription_update)$")


@router.get("/plans")
def get_plans():
    return billing.plans_payload()


@router.post("/checkout")
def post_checkout(body: CheckoutBody, request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """The hosted checkout for `plan`; the browser is sent to `url`. Back on success the webhook has (or shortly
    will have) applied the subscription, so the plan page re-reads /billing/me rather than trusting the redirect.
    An account with a live paid subscription is answered 409: the change belongs on that subscription (portal)."""
    base = settings.public_url.rstrip("/")
    if not billing.configured():
        raise HTTPException(503, "payments not configured")
    if billing.live_subscription(session, user, billing.provider().name) is not None:
        raise HTTPException(409, "subscription_exists")
    try:
        url = billing.provider().checkout_session(session, user, body.plan, f"{base}/plan?checkout=success&session_id={{CHECKOUT_SESSION_ID}}", f"{base}/plan?checkout=cancel")
    except billing.NotConfigured as exc:
        raise HTTPException(503, "payments not configured") from exc
    except billing.SubscriptionExists as exc:
        raise HTTPException(409, str(exc)) from exc
    except billing.BillingError as exc:
        raise HTTPException(502, str(exc)) from exc
    audit(session, "billing.checkout", actor=user.email, ip=client_ip(request), detail=body.plan)
    return {"url": url, "plan": body.plan}


@router.post("/portal")
def post_portal(request: Request, body: PortalBody | None = None, user: User = Depends(current_user), session: Session = Depends(get_session)):
    base = settings.public_url.rstrip("/")
    flow = body.flow if body else None
    try:
        url = billing.provider().portal_session(session, user, f"{base}/plan", flow)
    except billing.NotConfigured as exc:
        raise HTTPException(503, "payments not configured") from exc
    except billing.NoBillingAccount as exc:
        raise HTTPException(404, str(exc)) from exc
    except billing.BillingError as exc:
        raise HTTPException(502, str(exc)) from exc
    audit(session, "billing.portal", actor=user.email, ip=client_ip(request), detail=flow)
    return {"url": url}


@webhook_router.post("/webhook")
async def webhook(request: Request, session: Session = Depends(get_session)):
    """Raw body in (the signature covers the exact bytes, so no JSON parsing before verification), signature
    checked against the endpoint's signing secret, one event applied (once). A bad signature is 400; an unconfigured
    provider 503 — both make Stripe retry, and the log says which. The database work runs in the threadpool, as a
    sync route's would."""
    payload = await request.body()
    sig = request.headers.get("stripe-signature")
    try:
        out = await run_in_threadpool(billing.provider().handle_webhook, session, payload, sig)
    except billing.WebhookError as exc:
        raise HTTPException(400, str(exc)) from exc
    except billing.NotConfigured as exc:
        raise HTTPException(503, "payments not configured") from exc
    return {"received": True, **out}


@router.get("/me")
def get_me(user: User = Depends(current_user), session: Session = Depends(get_session)):
    return billing.me_payload(session, session.get(User, user.id))
