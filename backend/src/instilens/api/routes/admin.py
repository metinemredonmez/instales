from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from instilens.api.deps import current_user, get_session
from instilens.domain.models import User
from instilens.services import admin


def require_admin(user: User = Depends(current_user)) -> User:
    if user.role != "ADMIN":
        raise HTTPException(403, "admin only")
    return user


router = APIRouter(prefix="/api/v1/admin", tags=["admin"], dependencies=[Depends(require_admin)])


class UserPatch(BaseModel):
    plan: str | None = Field(None, pattern="^(FREE|PRO|PRO_PLUS)$")
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
def patch_user(user_id: int, body: UserPatch, session: Session = Depends(get_session)):
    out = admin.update_user(session, user_id, plan=body.plan, role=body.role, is_active=body.is_active)
    if out is None:
        raise HTTPException(404, "not found")
    return out


@router.get("/review")
def review(session: Session = Depends(get_session)):
    return admin.unverified(session)


@router.post("/review/verify")
def verify(body: VerifyBody, session: Session = Depends(get_session)):
    if not admin.verify(session, body.kind, body.id, body.name):
        raise HTTPException(404, "not found")
    return {"ok": True}


@router.post("/outcomes/compute")
def compute_outcomes(session: Session = Depends(get_session)):
    from instilens.services.outcomes import compute_outcomes as run

    return {"updated": run(session)}


_RUN_STATE: dict = {"running": False, "started_at": None, "finished_at": None, "result": None, "error": None}


def _run_pipeline_bg() -> None:
    import traceback
    from datetime import UTC, date, datetime

    from instilens.db.session import session_scope
    from instilens.ingestion.kap import build_kap_adapter
    from instilens.ingestion.prices.yahoo import load_prices
    from instilens.ingestion.sec import build_sec_adapter
    from instilens.services import pipeline
    from instilens.services.alerts import evaluate
    from instilens.services.outcomes import compute_outcomes

    _RUN_STATE.update(running=True, started_at=datetime.now(UTC).isoformat(), finished_at=None, result=None, error=None)
    try:
        out: dict = {}
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
            out["scored"] = pipeline.compute_intelligence(s, date.today())
            out["notifications"] = evaluate(s, date.today())
            from instilens.services.notify import deliver_pending

            out["delivered"] = deliver_pending(s)
            out["outcomes"] = compute_outcomes(s)
        _RUN_STATE["result"] = out
    except Exception:
        _RUN_STATE["error"] = traceback.format_exc()[-2000:]
    finally:
        _RUN_STATE.update(running=False, finished_at=datetime.now(UTC).isoformat())


@router.post("/pipeline/run")
def pipeline_run():
    """Kick off a full data pull in the background (same as `instilens run`). Admin only."""
    import threading

    if _RUN_STATE["running"]:
        return {"started": False, **_RUN_STATE}
    threading.Thread(target=_run_pipeline_bg, name="instilens-pipeline", daemon=True).start()
    return {"started": True, **_RUN_STATE}


@router.get("/pipeline/status")
def pipeline_status():
    return _RUN_STATE


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
    from instilens.services.news_rules import reapply_all

    return {"TR": reapply_all(session, "TR"), "US": reapply_all(session, "US")}


@router.post("/news/enrich")
def enrich_news(market: str = "TR", session: Session = Depends(get_session)):
    from instilens.ai.news_enrich import enrich

    return {"tagged": enrich(session, market)}
