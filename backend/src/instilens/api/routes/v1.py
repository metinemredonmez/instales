import asyncio
import json
import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session
from sse_starlette.sse import EventSourceResponse

from instilens.ai import build_engine
from instilens.api.deps import current_user, get_session, require_admin, require_plan, ticket_user
from instilens.api.hardening import client_ip, hit
from instilens.config import settings
from instilens.db.session import session_scope
from instilens.domain.models import User
from instilens.ingestion.prices.provider import ProviderUnavailable
from instilens.services import analytics, candles, live, search

log = logging.getLogger("instilens.api")

# Every data route requires a signed-in user; /auth/* and /health live outside this router.
router = APIRouter(prefix="/api/v1", tags=["intelligence"], dependencies=[Depends(current_user)])
# Routes a browser reaches without headers (EventSource, <audio>): authenticated by a short-lived ticket instead.
ticket_router = APIRouter(prefix="/api/v1", tags=["intelligence"])


def _warm_audio_later(note) -> None:
    """Synthesise the default voices for a just-written note in a background thread (never blocks the request)."""
    import threading

    from instilens.ai.tts import note_text, provider, synthesize

    if note is None or provider() is None:
        return
    text = note_text(note, session=None)
    lang = note.lang

    def run() -> None:
        for g in ("female", "male"):
            try:
                synthesize(text, lang=lang, gender=g)
            except Exception:  # noqa: BLE001 — warming is best-effort
                continue

    threading.Thread(target=run, daemon=True).start()


def _ai_budget(request: Request, user: User, session: Session) -> None:
    """Paid model calls: per-user hourly cap (ASVS 7.17 — expensive endpoints get their own budget) and, while
    plans are enforced, the plan's daily quota (services/plans `ai_research_per_day`; 402 plan_limit once it is
    used up). Both ride on the shared counter store; the daily count is read before either is charged, so a call
    the quota refuses never spends an hourly slot."""
    from instilens.api.hardening import failures
    from instilens.services import plans

    daily = plans.limit(session, user, "ai_research_per_day")
    if daily is not None and failures(f"aiday:{user.id}", 86400) >= daily:
        plan = plans.effective_plan(session, user)[0]
        raise plans.PlanLimit("ai_research_per_day", plan, daily, plans.upgrade_for("ai_research_per_day", plan))
    if not hit(f"ai:{user.id}", settings.ai_requests_per_hour, 3600):
        raise HTTPException(429, "AI request budget exhausted for this hour", headers={"Retry-After": "3600"})
    if daily is not None:
        hit(f"aiday:{user.id}", daily, 86400)
    log.info("ai call user=%s ip=%s path=%s", user.id, client_ip(request), request.url.path)

MarketParam = Query("TR", pattern="^(TR|US)$")
LangParam = Query("tr", pattern="^(tr|en)$")
PeriodParam = Query("annual", pattern="^(annual|quarterly)$")


@router.get("/radar")
def get_radar(market: str = MarketParam, limit: int = Query(20, ge=1, le=100), window: int | None = Query(None, ge=1, le=365), session: Session = Depends(get_session)):
    """Default window = the market's score window (TR 30D, US 100D). Other windows re-aggregate flows on the fly."""
    data = analytics.radar(session, market, limit)
    if window is not None and window != data.get("window_days"):
        flows = analytics.window_flows(session, market, window)
        data.update({"window_days": window, "window_start": flows["window_start"], "accumulated": flows["accumulated"][:limit], "distributed": flows["distributed"][:limit]})
    return data


@router.get("/moves")
def get_moves(
    kind: analytics.MoveKind = Query(...),
    market: str = MarketParam,
    window: int | None = Query(None, ge=1, le=730),
    fund: str | None = Query(None, max_length=16),
    limit: int = Query(25, ge=1, le=100),
    session: Session = Depends(get_session),
):
    """Top buys / sells / new positions / exits of the window with the parties behind each row. Default window =
    the market's score window (TR 30D, US 100D); `fund` narrows the rows to that fund's own moves."""
    data = analytics.moves(session, market, kind=kind, window_days=window, fund_code=fund, limit=limit)
    if data is None:
        raise HTTPException(404, "fund not found")
    return data


@router.get("/news")
def get_news(market: str = MarketParam, symbol: str | None = Query(None, max_length=16), limit: int = Query(40, ge=1, le=200), session: Session = Depends(get_session)):
    return analytics.news(session, market, symbol, limit)


@router.get("/freshness")
def get_freshness(market: str = MarketParam, session: Session = Depends(get_session)):
    return analytics.data_freshness(session, market)


@router.get("/signals/performance")
def get_signal_performance(market: str = MarketParam, session: Session = Depends(get_session)):
    return analytics.signal_performance(session, market)


@router.get("/institutions")
def get_institutions(market: str = MarketParam, session: Session = Depends(get_session)):
    return analytics.institutions(session, market)


@router.get("/institutions/{code}")
def get_institution(code: str, market: str = MarketParam, session: Session = Depends(get_session)):
    data = analytics.institution_detail(session, market, code)
    if data is None:
        raise HTTPException(404, "institution not found")
    return data


@router.get("/funds/overlap")  # registered before /funds/{code}, or "overlap" would be read as a fund code
def get_fund_overlap(codes: str = Query(..., min_length=1, max_length=120, description="2..6 fund codes of one market, comma-separated"), session: Session = Depends(get_session)):
    """How far two to six funds' latest books overlap: every pair's symbol overlap (common / union) and weighted
    overlap (Σ min weight over the common symbols; null when a fund reports no weight for one of them), plus the
    symbols every requested fund holds with each fund's weight. 404 when a code is unknown, 422 with fewer than two
    codes, more than six, or funds of two markets."""
    from instilens.services import ownership

    try:
        data = ownership.fund_overlap(session, codes.split(","))
    except ownership.OverlapRequestError as exc:
        raise HTTPException(422, str(exc)) from exc
    if data is None:
        raise HTTPException(404, "fund not found")
    return data


@router.get("/funds/{code}/compare/{other}")
def get_fund_compare(code: str, other: str, session: Session = Depends(get_session)):
    data = analytics.compare_funds(session, code, other)
    if data is None:
        raise HTTPException(404, "fund not found")
    return data


@router.get("/stocks/{symbol}")
def get_stock(symbol: str, market: str = MarketParam, session: Session = Depends(get_session)):
    data = analytics.stock_detail(session, market, symbol)
    if data is None:
        raise HTTPException(404, "instrument not found")
    return data


@router.get("/stocks/{symbol}/ownership")
def get_stock_ownership(symbol: str, market: str = MarketParam, limit: int = Query(50, ge=1, le=500), session: Session = Depends(get_session)):
    """Who holds the stock: one row per fund from its LATEST portfolio report (never two dates of one fund), largest
    quantity first, with the fund's weight, the share of the company it holds (null until the share count is known),
    its last move and the report's confidence. Reports older than two reporting periods for the market (TR 60 days,
    US 182) are left out and counted in `stale_holders`. `totals` add every counted holder; `top10_pct_of_held` and
    `hhi` describe how concentrated the held quantity is; `crowding` is the stored CROWDING score with its
    explanation (null until the pipeline has computed one). Reported positions, not recommendations."""
    from instilens.services import ownership

    data = ownership.stock_ownership(session, market, symbol, limit)
    if data is None:
        raise HTTPException(404, "instrument not found")
    return data


@router.get("/stocks/{symbol}/fundamentals")
def get_stock_fundamentals(symbol: str, market: str = MarketParam, period: str = PeriodParam, session: Session = Depends(get_session)):
    """Reported fundamentals of a stock: the provider's latest metrics snapshot, its income / balance / cashflow
    statements for `period` (newest first, at most 8 each) and the ratios derived from them. Statement lines and
    the snapshot's TTM figures are absolute in `currency` (the reporting currency); the snapshot's market cap, EV,
    52-week range and EPS are in `snapshot.quote_currency` (the listing currency). Percentages already ×100,
    anything the provider did not report is null. A known symbol nothing has been fetched for yet answers with
    `snapshot: null` and empty statements, never invented numbers."""
    from instilens.services import fundamentals

    data = fundamentals.stock_fundamentals(session, market, symbol, period)  # type: ignore[arg-type]  # PeriodParam validates the literal
    if data is None:
        raise HTTPException(404, "instrument not found")
    return data


@router.get("/stocks/{symbol}/insiders")
def get_stock_insiders(symbol: str, market: str = MarketParam, days: int = Query(90, ge=30, le=730), session: Session = Depends(get_session)):
    """Insider transactions of the stock over the last `days` — SEC Form 4 for a US issuer, KAP "Pay Alım Satım
    Bildirimi" of directors, executives and shareholders for a BIST company (`source` sec-edgar / kap): a summary
    (distinct insiders with open-market purchases / sales, their values, the 30-day purchase cluster if any) and every
    reported row, newest first, each with its transaction code as filed (P purchase, S sale, A grant, M exercise /
    RSU settlement, F tax withholding, G gift …; KAP rows are P / S only), price when stated (null otherwise, never
    looked up; a KAP range stays a `price_range`), `party_kind` and `post_pct_stake` on KAP rows, `buyback` for a
    company's own-share rows (listed, not counted), the accession / KAP index and the filing URL; `coverage_since`
    (BIST) is the earliest day the KAP feed has read and `more_url` the issuer's list at the source. Reported facts,
    not recommendations."""
    from instilens.services import insiders

    data = insiders.stock_insiders(session, market, symbol, days)
    if data is None:
        raise HTTPException(404, "instrument not found")
    return data


@router.get("/stocks/{symbol}/filings")
def get_stock_filings(symbol: str, market: str = MarketParam, form: str | None = Query(None, pattern="^(8-K|10-K|10-Q|4|4/A)$"), limit: int = Query(20, ge=1, le=100), session: Session = Depends(get_session)):
    """The issuer's recent EDGAR filings (Form 4, 8-K with its item codes, 10-K, 10-Q), newest first, with the
    filing index URL and the primary document. `form` narrows to one type. TR symbols answer `supported: false`."""
    from instilens.services import insiders

    data = insiders.stock_filings(session, market, symbol, form, limit)
    if data is None:
        raise HTTPException(404, "instrument not found")
    return data


@router.get("/stocks/{symbol}/ai")
def get_stock_ai(symbol: str, request: Request, market: str = MarketParam, refresh: bool = False, lang: str = LangParam, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """AI note for the stock (flows × headlines). Cached per day and language; refresh=true (admin) regenerates."""
    from instilens.ai.assess import AiUnavailable, note_json, stock_assessment

    if refresh:
        require_admin(user)
    try:  # the budget is charged inside, only when the model is really called (first generation or refresh) — never on a cache hit
        note = stock_assessment(session, market, symbol, force=refresh, lang=lang, budget=lambda: _ai_budget(request, user, session))
    except AiUnavailable as exc:
        raise HTTPException(503, f"ai unavailable: {exc}") from exc
    _warm_audio_later(note)
    if note is None:
        raise HTTPException(404, "no assessment available (unknown symbol or AI disabled)")
    return note_json(note)


@router.get("/brief")
def get_brief(request: Request, market: str = MarketParam, refresh: bool = False, lang: str = LangParam, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """Morning brief for the market. Generated by the scheduler at 08:30 (TR + EN); refresh=true (admin) regenerates now."""
    from instilens.ai.assess import AiUnavailable, daily_brief, note_json

    if refresh:
        require_admin(user)
    try:  # budget charged only on a real model call, never on a cache hit
        note = daily_brief(session, market, force=refresh, lang=lang, budget=lambda: _ai_budget(request, user, session))
    except AiUnavailable as exc:
        raise HTTPException(503, f"ai unavailable: {exc}") from exc
    if refresh:
        _warm_audio_later(note)
    return note_json(note)


@ticket_router.get("/ai-notes/{note_id}/audio", dependencies=[Depends(require_plan("tts", ticket=True))])
def get_note_audio(note_id: int, gender: str = Query("female", pattern="^(female|male)$"), voice: str | None = Query(None, max_length=64), user: User = Depends(ticket_user), session: Session = Depends(get_session)):
    """MP3 narration of an AI note. Cached → file (seekable). Not cached → streamed while ElevenLabs synthesises,
    so playback starts within a second or two instead of after the whole note is rendered. A plan feature (`tts`)
    while plans are enforced: 402 plan_limit otherwise."""
    from fastapi.responses import FileResponse, StreamingResponse

    from instilens.ai.tts import cached_path, note_text, provider, stream, voice_allowed
    from instilens.domain.models import AiNote

    note = session.get(AiNote, note_id)
    if note is None:
        raise HTTPException(404, "note not found")
    if provider() is None:
        raise HTTPException(404, "tts not configured")
    if voice and not voice_allowed(note.lang, voice):
        raise HTTPException(400, "voice not available for this language")
    text = note_text(note, session)
    cached = cached_path(text, lang=note.lang, gender=gender, voice_id=voice)
    if cached:
        return FileResponse(cached, media_type="audio/mpeg", filename=f"instilens-{note.kind.lower()}-{note.as_of}.mp3")
    return StreamingResponse(stream(text, lang=note.lang, gender=gender, voice_id=voice), media_type="audio/mpeg",
                             headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"})


PREVIEW_TEXT = {"tr": "Merhaba, ben InstiLens. Sabah brifingini ve hisse notlarını bu sesle okuyacağım.",
                "en": "Hi, I'm InstiLens. I'll read the morning brief and stock notes in this voice."}


@ticket_router.get("/tts/preview/{voice_id}", dependencies=[Depends(require_plan("tts", ticket=True))])
def tts_preview(voice_id: str, lang: str = LangParam, user: User = Depends(ticket_user)):
    """A two-sentence sample in the given voice, so the picker can audition voices instantly (cached on disk)."""
    from fastapi.responses import FileResponse

    from instilens.ai.tts import provider, synthesize, voice_allowed

    if provider() != "elevenlabs":
        raise HTTPException(404, "tts not configured")
    if not voice_allowed(lang, voice_id):
        raise HTTPException(400, "voice not available for this language")
    path = synthesize(PREVIEW_TEXT["en" if lang == "en" else "tr"], lang=lang, gender="female", voice_id=voice_id)
    if path is None:
        raise HTTPException(404, "tts not configured")
    return FileResponse(path, media_type="audio/mpeg")


@router.get("/live-tv")
def live_tv():
    """Channels for the live TV widget (YouTube live embeds); editable in Admin → Ayarlar."""
    out = []
    for part in (settings.live_tv_channels or "").split(","):
        name, _, cid = part.strip().partition("|")
        if name and cid:
            out.append({"name": name.strip(), "channel_id": cid.strip(), "embed": f"https://www.youtube-nocookie.com/embed/live_stream?channel={cid.strip()}&autoplay=1&mute=1"})
    return out


@router.get("/quotes")
def get_quotes(session: Session = Depends(get_session)):
    """Header quotes (USD/TRY, EUR/TRY, BIST 100, S&P 500) from the active price provider (each quote names its
    `source` and says whether it is `delayed`), cached 60 s server-side, plus the open/closed state of BIST and
    NYSE. A quote the provider cannot answer is omitted, or carried over from the last good fetch with
    `stale: true`. While `instilens feed` runs, the same payload also arrives as a `quotes` live event."""
    from instilens.services import quotes, runtime_settings

    runtime_settings.apply(session)  # a provider switch may have landed on the other uvicorn worker; one small-table read
    return quotes.snapshot()


@router.get("/tts/status")
def tts_status():
    from instilens.ai.tts import provider

    return {"provider": provider()}


@router.get("/tts/voices")
def tts_voices(lang: str = LangParam):
    """Selectable narration voices for a language (configured + admin extras + verified library voices)."""
    from instilens.ai.tts import provider, voices

    return {"provider": provider(), "lang": lang, "voices": voices(lang) if provider() == "elevenlabs" else []}


@router.get("/stocks/{symbol}/timeline")
def get_stock_timeline(symbol: str, market: str = MarketParam, session: Session = Depends(get_session)):
    data = analytics.stock_timeline(session, market, symbol)
    if data is None:
        raise HTTPException(404, "instrument not found")
    return data


@router.get("/stocks/{symbol}/series")
def get_stock_series(symbol: str, market: str = MarketParam, session: Session = Depends(get_session)):
    data = analytics.stock_series(session, market, symbol)
    if data is None:
        raise HTTPException(404, "instrument not found")
    return data


@router.get("/stocks/{symbol}/candles")
def get_stock_candles(
    symbol: str,
    market: str = MarketParam,
    interval: candles.Interval = Query("1d"),
    lookback: int = Query(candles.LOOKBACK_DEFAULT, ge=candles.LOOKBACK_MIN, le=candles.LOOKBACK_MAX, description="most bars returned, oldest first"),
    session: Session = Depends(get_session),
):
    """OHLCV bars for the chart widget: daily from `market_prices` when the table holds full OHLC for the range,
    otherwise (and always below a day) from the active price provider, cached 60 s server-side with the last good
    answer served through an outage. The payload names its `source` and says whether it is `delayed`; `t` is
    epoch seconds UTC and `tz` the exchange's zone. 503 when the provider must answer and cannot."""
    from instilens.services import runtime_settings

    runtime_settings.apply(session)  # like /quotes: a provider switch may have landed on the other uvicorn worker
    try:
        data = candles.candles(session, market, symbol, interval, lookback)
    except ProviderUnavailable as exc:
        log.warning("candles %s %s %s: %s", market, symbol, interval, exc)
        raise HTTPException(503, "provider unavailable") from exc
    if data is None:
        raise HTTPException(404, "instrument not found")
    return data


@router.get("/funds/{code}")
def get_fund(code: str, session: Session = Depends(get_session)):
    data = analytics.fund_detail(session, code)
    if data is None:
        raise HTTPException(404, "fund not found")
    return data


@router.get("/events")
def get_events(market: str = MarketParam, limit: int = Query(50, ge=1, le=200), session: Session = Depends(get_session)):
    return analytics.events(session, market, limit)


@ticket_router.get("/events/stream")
async def stream_events(request: Request, market: str = MarketParam, poll_seconds: float = Query(3.0, ge=2.0, le=60.0), after: int | None = Query(None, ge=0), user: User = Depends(ticket_user)):
    """One live stream per tab. Two things ride on it: KAP/SEC transactions as full rows (`transaction`, the Live page)
    and small "something changed" events the SPA turns into query refetches (`notification`, `compute`, `news`,
    `brief`, `pipeline` — see services/live), plus `quotes`, which carries the whole /quotes payload so the header
    strip is replaced without a refetch. Tailing the database every few seconds *is* the fan-out: the scheduler, the
    feed, the admin worker thread and both API workers only ever append rows, so no broker is involved. `after` (or
    the Last-Event-ID header on a browser-initiated reconnect) replays change events missed while disconnected."""
    owner = str(user.id)
    header = request.headers.get("last-event-id", "")
    replay_from = after if after is not None else (int(header) if header.isdigit() else None)

    def opening() -> tuple[int, int]:
        with session_scope() as s:
            latest = analytics.events(s, market, limit=1)
            return (latest[0]["id"] if latest else 0), (replay_from if replay_from is not None else live.latest_id(s))

    def tick(last_tx: int, last_live: int) -> tuple[list[dict], list[dict]]:
        with session_scope() as s:
            fresh = analytics.events(s, market, limit=100, after_id=last_tx)
            changes = [{"id": ev.id, "kind": ev.kind, "market": ev.market_code, **(ev.payload or {})} for ev in live.since(s, last_live, market=market, owner_id=owner)]
            return fresh, changes

    async def generator():
        last_tx, last_live = await asyncio.to_thread(opening)
        yield {"event": "ready", "data": json.dumps({"last_id": last_tx, "live_id": last_live})}  # flushes headers through proxies
        while not await request.is_disconnected():
            fresh, changes = await asyncio.to_thread(tick, last_tx, last_live)  # DB work off the event loop
            for ev in fresh:
                last_tx = max(last_tx, ev["id"])
                yield {"event": "transaction", "data": json.dumps(ev)}
            for ch in changes:
                last_live = max(last_live, ch["id"])
                yield {"event": ch["kind"], "id": str(ch["id"]), "data": json.dumps(ch)}
            await asyncio.sleep(poll_seconds)

    return EventSourceResponse(generator())


@router.get("/search")
def get_search(q: str = Query("", max_length=64), market: str = MarketParam, session: Session = Depends(get_session)):
    """Header typeahead: stocks, funds and institutions of the selected market."""
    return analytics.search(session, market, q)


@router.get("/search/text")
def get_text_search(
    q: str = Query(..., min_length=search.Q_MIN, max_length=search.Q_MAX),
    market: str = MarketParam,
    kinds: str | None = Query(None, max_length=64, description="comma-separated subset of disclosure,filing,news,note; omit for every kind"),
    limit: int = Query(20, ge=1, le=50),
    session: Session = Depends(get_session),
):
    """Full text over the market's documents — KAP/SEC disclosures, the EDGAR filing index, headlines (with their AI
    summary) and AI notes — ranked by relevance then date; the snippet marks the matched terms with «» (services/search)."""
    try:
        wanted = search.parse_kinds(kinds)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return search.text_search(session, market, q, wanted, limit)


@router.get("/screener")
def get_screener(
    market: str = MarketParam,
    min_smart_money_score: float | None = None,
    min_consensus_score: float | None = None,
    min_funds_increasing: int | None = None,
    min_funds_new: int | None = None,
    min_net_flow_value: float | None = None,
    max_price_change_30d_pct: float | None = None,
    signal: list[str] | None = Query(None),
    limit: int = Query(25, ge=1, le=200),
    session: Session = Depends(get_session),
):
    return analytics.screener(
        session,
        market,
        min_smart_money_score=min_smart_money_score,
        min_consensus_score=min_consensus_score,
        min_funds_increasing=min_funds_increasing,
        min_funds_new=min_funds_new,
        min_net_flow_value=min_net_flow_value,
        max_price_change_pct=max_price_change_30d_pct,
        signal_types=signal,
        limit=limit,
    )


class ResearchRequest(BaseModel):
    question: str = Field(min_length=3, max_length=2000)
    market: str = Field("TR", pattern="^(TR|US)$")


@router.post("/research")
def post_research(body: ResearchRequest, request: Request, user: User = Depends(current_user), session: Session = Depends(get_session)):
    """AI research: natural-language question → tool-grounded answer with an audit trail. Per-user hourly budget,
    plus the plan's daily quota while plans are enforced."""
    _ai_budget(request, user, session)
    answer = build_engine(session).ask(body.question, body.market)
    return {
        "question": answer.question,
        "answer": answer.answer,
        "model": answer.model,
        "usage": answer.usage,
        "tool_calls": [{"name": c.name, "input": c.input, "output_preview": c.output_preview} for c in answer.tool_calls],
        "unverified_numbers": answer.unverified_numbers,  # local engine: figures in the answer no tool result carries (never empty silently)
    }
