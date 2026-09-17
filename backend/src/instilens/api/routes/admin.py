from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from instilens.api.deps import get_session, require_admin
from instilens.api.hardening import client_ip
from instilens.domain.models import AuditEvent, User
from instilens.services import admin, billing
from instilens.services.auth import audit

router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class UserPatch(BaseModel):
    plan: str | None = Field(None, pattern="^(FREE|PRO|PRO_PLUS)$")
    plan_until: date | None = None  # the manual grant runs out at the end of this day (null = open-ended); alone, it re-dates the grant the account has
    plan_note: str | None = Field(None, max_length=200)  # with `plan`: why (kept on the subscription row)
    role: str | None = Field(None, pattern="^(USER|ADMIN)$")
    is_active: bool | None = None


class VerifyBody(BaseModel):
    kind: str = Field(pattern="^(instrument|fund|institution)$")
    id: int
    name: str | None = Field(None, max_length=256)


@router.get("/users")
def users(session: Session = Depends(get_session)):
    return admin.list_users(session)


@router.patch("/users/{user_id}")
def patch_user(user_id: int, body: UserPatch, request: Request, actor: User = Depends(require_admin), session: Session = Depends(get_session)):
    target = session.get(User, user_id)
    if target is None:
        raise HTTPException(404, "not found")
    demoting = (body.role is not None and body.role != "ADMIN") or body.is_active is False
    if demoting and target.id == actor.id:
        raise HTTPException(400, "you cannot demote or deactivate your own account")
    if demoting and target.role == "ADMIN":
        admins = session.scalar(select(func.count(User.id)).where(User.role == "ADMIN", User.is_active.is_(True))) or 0
        if admins <= 1:
            raise HTTPException(400, "cannot remove the last active admin")
    if body.plan_until is not None and body.plan_until < date.today():
        raise HTTPException(400, "plan_until is in the past")
    try:
        out = admin.update_user(session, user_id, plan=body.plan, role=body.role, is_active=body.is_active, plan_until=body.plan_until, plan_note=body.plan_note,
                                redate="plan_until" in body.model_fields_set, actor=actor.email)
    except billing.BillingError as exc:
        raise HTTPException(400, str(exc)) from exc
    if body.role is not None or body.is_active is not None:
        target.token_version = (target.token_version or 1) + 1  # role/access changes take effect on the next request
    audit(session, "admin.user_patch", actor=actor.email, subject=target.email, ip=client_ip(request), detail=body.model_dump_json(exclude_none=True))
    return out


@router.get("/settings")
def get_runtime_settings(session: Session = Depends(get_session)):
    """Editable (non-secret) settings with their current value, default and override status."""
    from instilens.services import runtime_settings

    return runtime_settings.snapshot(session)


@router.put("/settings")
def put_runtime_settings(body: dict, request: Request, actor: User = Depends(require_admin), session: Session = Depends(get_session)):
    from instilens.services import runtime_settings

    try:
        changed = runtime_settings.set_many(session, body, actor.email)
    except runtime_settings.SettingError as exc:
        raise HTTPException(400, str(exc)) from exc
    if changed:
        audit(session, "admin.settings", actor=actor.email, ip=client_ip(request), detail=", ".join(changed))
    return {"changed": changed, "settings": runtime_settings.snapshot(session)}


# ---------------------------------------------------------------- desktop releases
@router.get("/releases")
def list_releases(session: Session = Depends(get_session)):
    from instilens.config import settings
    from instilens.domain.models import Release
    from instilens.services import releases

    rows = session.scalars(select(Release).order_by(Release.created_at.desc())).all()
    return {"platforms": [{"key": k, "label": releases.PLATFORM_LABEL[k]} for k in releases.PLATFORMS],
            "updater_pubkey_set": bool(settings.desktop_updater_pubkey), "upload_key_set": bool(settings.release_upload_key),
            "releases": [releases.release_json(r, settings.public_url) for r in rows]}


class ReleasePatch(BaseModel):
    status: str | None = Field(None, pattern="^(DRAFT|PUBLISHED|WITHDRAWN)$")
    notes: str | None = Field(None, max_length=4000)


@router.patch("/releases/{release_id}")
def patch_release(release_id: int, body: ReleasePatch, request: Request, actor: User = Depends(require_admin), session: Session = Depends(get_session)):
    from instilens.config import settings
    from instilens.domain.models import Release
    from instilens.services import releases

    rel = session.get(Release, release_id)
    if rel is None:
        raise HTTPException(404, "not found")
    if body.notes is not None:
        rel.notes = body.notes
    if body.status:
        try:
            releases.set_status(session, rel, body.status)
        except releases.ReleaseError as exc:
            raise HTTPException(400, str(exc)) from exc
        audit(session, "admin.release", actor=actor.email, subject=rel.version, ip=client_ip(request), detail=body.status)
    return releases.release_json(rel, settings.public_url)


@router.delete("/releases/{release_id}", status_code=204)
def delete_release(release_id: int, request: Request, actor: User = Depends(require_admin), session: Session = Depends(get_session)):
    """Only drafts can be deleted; published versions are withdrawn instead (someone may have installed them)."""
    import shutil

    from instilens.config import settings
    from instilens.domain.models import Release

    rel = session.get(Release, release_id)
    if rel is None:
        raise HTTPException(404, "not found")
    if rel.status != "DRAFT":
        raise HTTPException(400, "withdraw published releases instead of deleting them")
    shutil.rmtree(settings.releases_dir / rel.version, ignore_errors=True)
    session.delete(rel)
    audit(session, "admin.release", actor=actor.email, subject=rel.version, ip=client_ip(request), detail="DELETED")


@router.get("/audit")
def audit_events(limit: int = Query(100, ge=1, le=500), session: Session = Depends(get_session)):
    """Security events, newest first (logins, lockouts, password/role changes)."""
    rows = session.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(limit)).all()
    return [{"id": e.id, "kind": e.kind, "actor": e.actor, "subject": e.subject, "ip": e.ip, "detail": e.detail, "created_at": e.created_at.isoformat()} for e in rows]


@router.get("/review")
def review(session: Session = Depends(get_session)):
    return admin.unverified(session)


@router.post("/review/verify")
def verify(body: VerifyBody, session: Session = Depends(get_session)):
    if not admin.verify(session, body.kind, body.id, body.name):
        raise HTTPException(404, "not found")
    return {"ok": True}


@router.get("/waitlist")
def waitlist(session: Session = Depends(get_session)):
    from instilens.domain.models import WaitlistEntry

    rows = session.scalars(select(WaitlistEntry).order_by(WaitlistEntry.created_at.desc())).all()
    return [{"id": w.id, "email": w.email, "name": w.name, "lang": w.lang, "source": w.source, "created_at": w.created_at.isoformat()} for w in rows]


@router.get("/config")
def config_status():
    """Which integrations are configured on this server — booleans and public values only, never secrets."""
    from instilens.ai.tts import provider as tts_provider
    from instilens.config import settings

    return {
        "environment": settings.environment,
        "public_url": settings.public_url,
        "allow_registration": settings.allow_registration,
        "database": settings.database_url.split(":", 1)[0],
        "kap_adapter": settings.kap_adapter,
        "kap_api_base_url": settings.kap_api_base_url if settings.kap_adapter == "api" else None,
        "sec_adapter": settings.sec_adapter,
        "sec_ciks": settings.sec_ciks,
        "ai": {"provider": settings.ai_provider, "model": settings.ai_local_model if settings.ai_provider == "local" else settings.ai_model,
               "configured": bool(settings.ai_local_model) if settings.ai_provider == "local" else bool(settings.anthropic_api_key), "news_enrich": settings.ai_news_enabled},
        "tts": {"provider": tts_provider(), "voices": {k: bool(getattr(settings, f"elevenlabs_voice_{k}", "")) for k in ("tr_female", "tr_male", "en_female", "en_male")}},
        "news": {"enabled": settings.news_enabled, "newsapi": bool(settings.newsapi_key)},
        "channels": {"telegram": bool(settings.telegram_bot_token), "email": bool(settings.smtp_host), "web_push": bool(settings.vapid_public_key), "onesignal": bool(settings.onesignal_app_id)},
        "billing": {"provider": billing.provider().name, "configured": billing.configured(), "plans_enforced": settings.plans_enforced,
                    "prices": {"PRO": bool(settings.stripe_price_pro), "PRO_PLUS": bool(settings.stripe_price_pro_plus)}},
    }


@router.get("/providers")
def providers(session: Session = Depends(get_session)):
    """Price provider in use (after the unconfigured-choice fallback) with its honest status, plus the quote
    feed's heartbeat. `selected` is the runtime setting as chosen, `active` what actually answers. The overrides
    are re-applied first: with two uvicorn workers the PUT that switched the provider landed on only one of them.
    `status` is the provider as the feed process saw it on its last run while the feed is alive (`status_from:
    "feed"` — that process drives the strip; this worker's own instance may never have fetched), otherwise this
    worker's instance (`"api"`)."""
    from instilens.config import settings
    from instilens.ingestion.prices.provider import PROVIDERS, resolve_provider
    from instilens.services import feed, runtime_settings

    runtime_settings.apply(session)
    provider = resolve_provider()
    fb = feed.status(session)
    seen_by_feed = fb.pop("provider_status", None)
    from_feed = bool(fb["running"] and seen_by_feed and seen_by_feed.get("name") == provider.name)
    return {
        "price": {"active": provider.name, "selected": settings.price_provider, "available": list(PROVIDERS),
                  "status": seen_by_feed if from_feed else provider.status().as_dict(), "status_from": "feed" if from_feed else "api"},
        "feed": fb,
    }


@router.post("/outcomes/compute")
def compute_outcomes(session: Session = Depends(get_session)):
    from instilens.services.outcomes import compute_outcomes as run

    return {"updated": run(session)}


def _run_pipeline_bg(run_id: int) -> None:
    """Runs under the database lock row `run_id` (see hardening.acquire_pipeline_lock) so several workers never overlap."""
    import traceback

    from instilens.api.hardening import finish_pipeline_run
    from instilens.db.session import session_scope
    from instilens.ingestion.kap import build_kap_adapter
    from instilens.ingestion.prices import load_prices
    from instilens.ingestion.sec import build_sec_adapter
    from instilens.services import live, pipeline
    from instilens.services.alerts import evaluate
    from instilens.services.outcomes import compute_outcomes

    try:
        out: dict = {}
        with session_scope() as s:
            live.publish(s, "pipeline", payload={"status": "running", "run_id": run_id})
        with session_scope() as s:
            out["kap_ingested"] = pipeline.ingest(s, build_kap_adapter())
            out["sec_ingested"] = pipeline.ingest(s, build_sec_adapter())
            out["parsed"] = pipeline.parse_pending(s)
            from instilens.config import settings as _settings
            from instilens.ingestion.news import fetch_feeds

            for m in ("TR", "US"):
                out[f"prices_{m}"] = load_prices(s, m, days=60)
                out[f"news_{m}"] = fetch_feeds(s, m, newsapi_key=_settings.newsapi_key)
            out["position_changes"] = pipeline.rebuild_positions(s)
            if _settings.sec_form4_enabled:  # Form 4 insider rows + issuer filings for the issuers the 13F diffs touched; per-issuer failures are logged inside
                from instilens.services.insiders import refresh as refresh_insiders

                s.commit()  # the run so far is durable before the issuer walk (which commits issuer by issuer)
                out["form4"] = refresh_insiders(s)["form4"]
            out["scored"] = pipeline.compute_intelligence(s, date.today())
            out["notifications"] = evaluate(s, date.today())
            from instilens.services.notify import deliver_pending

            out["delivered"] = deliver_pending(s)
            out["outcomes"] = compute_outcomes(s)
            live.publish(s, "compute", payload={"scored": out["scored"]})
            for m in ("TR", "US"):
                if out.get(f"news_{m}"):
                    live.publish(s, "news", market=m, payload={"added": out[f"news_{m}"]})
            live.prune(s)
        finish_pipeline_run(run_id, result=out)
        with session_scope() as s:
            live.publish(s, "pipeline", payload={"status": "done", "run_id": run_id})
    except Exception:
        finish_pipeline_run(run_id, error=traceback.format_exc()[-2000:])
        with session_scope() as s:
            live.publish(s, "pipeline", payload={"status": "failed", "run_id": run_id})


@router.post("/pipeline/run")
def pipeline_run(actor: User = Depends(require_admin)):
    """Kick off a full data pull in the background (same as `instilens run`). Admin only.
    The `pipeline_runs` row is the lock: a second admin (or worker) gets started=false while one is running."""
    import threading

    from instilens.api.hardening import acquire_pipeline_lock, pipeline_run_status

    run_id = acquire_pipeline_lock(actor.email)
    if run_id is None:
        return {"started": False, **pipeline_run_status()}
    threading.Thread(target=_run_pipeline_bg, args=(run_id,), name="instilens-pipeline", daemon=True).start()
    return {"started": True, **pipeline_run_status()}


@router.get("/pipeline/status")
def pipeline_status():
    from instilens.api.hardening import pipeline_run_status

    return pipeline_run_status()


# ---------------------------------------------------------------------- news rules (Newsomatic-style)


class NewsRuleBody(BaseModel):
    name: str = Field(min_length=2, max_length=64)
    market_code: str = Field("TR", pattern="^(TR|US)$")
    query: str = Field("", max_length=512)
    exclusion: str = Field("", max_length=512)
    only_sources: str = Field("", max_length=512)
    remove_sources: str = Field("", max_length=512)
    language: str = Field("", max_length=8)
    max_age_days: int = Field(3, ge=1, le=30)
    symbols: list[str] = Field(default_factory=list, max_length=20)
    newsapi_query: str = Field("", max_length=256)
    ai_summary: bool = True
    is_active: bool = True


def _rule_json(r) -> dict:
    return {k: getattr(r, k) for k in ("id", "name", "market_code", "query", "exclusion", "only_sources", "remove_sources", "language", "max_age_days", "symbols", "newsapi_query", "ai_summary", "is_active")}


@router.get("/news/rules")
def list_news_rules(session: Session = Depends(get_session)):
    from sqlalchemy import select

    from instilens.domain.models import NewsRule

    return [_rule_json(r) for r in session.scalars(select(NewsRule).order_by(NewsRule.id))]


@router.post("/news/rules", status_code=201)
def create_news_rule(body: NewsRuleBody, session: Session = Depends(get_session)):
    from instilens.domain.models import NewsRule

    r = NewsRule(**body.model_dump())
    session.add(r)
    session.flush()
    return _rule_json(r)


@router.put("/news/rules/{rule_id}")
def update_news_rule(rule_id: int, body: NewsRuleBody, session: Session = Depends(get_session)):
    from instilens.domain.models import NewsRule

    r = session.get(NewsRule, rule_id)
    if r is None:
        raise HTTPException(404, "not found")
    for k, v in body.model_dump().items():
        setattr(r, k, v)
    return _rule_json(r)


@router.delete("/news/rules/{rule_id}", status_code=204)
def delete_news_rule(rule_id: int, session: Session = Depends(get_session)):
    from instilens.domain.models import NewsRule

    r = session.get(NewsRule, rule_id)
    if r is None:
        raise HTTPException(404, "not found")
    session.delete(r)


@router.post("/news/rules/seed")
def seed_news_rules(session: Session = Depends(get_session)):
    from instilens.services.news_rules import seed_default_rules

    return {"created": seed_default_rules(session)}


@router.post("/news/reapply")
def reapply_news_rules(session: Session = Depends(get_session)):
    from datetime import UTC, datetime, timedelta

    from instilens.services import search_index
    from instilens.services.news_rules import reapply_all

    counts = {"TR": reapply_all(session, "TR"), "US": reapply_all(session, "US")}
    # The rules rewrote symbols/tags and deleted non-finance headlines without touching fetched_at: re-read the window
    # they covered (reapply_all's default, 7 days) so the search index follows; the news pass drops the orphans.
    search_index.reindex(session, since=datetime.now(UTC) - timedelta(days=7))
    return counts


@router.post("/news/enrich")
def enrich_news(market: str = "TR", session: Session = Depends(get_session)):
    from instilens.ai.news_enrich import enrich

    return {"tagged": enrich(session, market)}
