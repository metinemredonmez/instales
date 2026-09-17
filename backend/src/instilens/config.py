from pathlib import Path

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Runtime configuration. Every value can be overridden via INSTILENS_* env vars."""

    model_config = SettingsConfigDict(env_prefix="INSTILENS_", env_file=BACKEND_ROOT / ".env", extra="ignore")

    # SQLite keeps dev/test dependency-free; production uses postgresql+psycopg://...
    database_url: str = f"sqlite:///{BACKEND_ROOT / 'instilens.db'}"

    # KAP adapter: "fixture" reads JSON files from fixture_dir (prototype);
    # "api" talks to the official KAP Veri Yayın Servisi (needs a data-distribution contract).
    kap_adapter: str = "public"  # real data via the polite prototype; "api" once the licence is signed; "fixture" only for tests
    kap_fixture_dir: Path = BACKEND_ROOT / "fixtures" / "kap"
    # MKK API Portal → "KAP Data Dissemination Services". Dev gateway per the published OpenAPI spec;
    # switch to the production gateway URL once MKK provides it. Basic auth: key + secret from My Apps.
    kap_api_base_url: str = "https://apigwdev.mkk.com.tr/api/vyk"
    kap_api_key: str | None = None
    kap_api_secret: str | None = None
    kap_api_auth_header: str | None = None  # optional portal-issued Authorization value
    kap_api_auth_mode: str = "auto"  # auto-detected on first call; pin (e.g. "apikey") once `kap-test` reports it
    kap_api_rate_per_min: int = 6  # Free plan throttle
    kap_api_max_calls: int = 120  # per run (~20 min at 6/min); the scheduler runs often
    # "public" = prototype adapter over kap.org.tr (polite, capped). Dev/validation only.
    kap_public_days_back: int = 7
    kap_public_max_details: int = 25
    kap_public_max_reports: int = 40  # fund portfolio PDFs per run (2-3 requests each)
    kap_public_fund_codes: list[str] = []  # empty = all equity-focused funds seen in the window

    default_market: str = "TR"
    # Header quotes / market-hours badge: BIST closes on these ISO dates (exchange holidays are not modelled otherwise).
    market_holidays_tr: list[str] = []
    # Prices (header strip + daily market_prices). "yahoo" is delayed/unofficial (dev + beta); "matriks" is a slot
    # that stays "not configured" until the vendor documentation arrives (docs/08-price-providers.md) — an
    # unconfigured choice falls back to Yahoo. quotes_interval_s: how often `instilens feed` refreshes while a market is open.
    price_provider: str = "yahoo"
    quotes_interval_s: int = 60
    matriks_api_key: str | None = None
    matriks_base_url: str | None = None
    matriks_ws_url: str | None = None
    # Fundamentals (reported statements + trailing metrics, services/fundamentals). Only "yahoo" exists; the weekly
    # scheduler job (Sunday 06:00) is gated by fundamentals_enabled and asks for at most fundamentals_max_instruments
    # per market and run, stalest first — both editable from the admin UI.
    fundamentals_provider: str = "yahoo"
    fundamentals_enabled: bool = True
    fundamentals_max_instruments: int = 300

    # SEC EDGAR (Global). Free; SEC requires an identifying User-Agent "AppName contact@email".
    sec_adapter: str = "edgar"  # live EDGAR (free); "fixture" only for tests
    sec_fixture_dir: Path = BACKEND_ROOT / "fixtures" / "sec"
    sec_user_agent: str = "InstiLens research@instilens.app"
    sec_ciks: list[str] = ["1067983", "1350694", "1037389"]  # Berkshire, Bridgewater, Renaissance — edit freely
    # Form 4 insider transactions + issuer filings (services/insiders). The daily job (08:30, after EDGAR's overnight
    # window) is gated by sec_form4_enabled and asks EDGAR for at most sec_form4_max_issuers issuers per run, stalest
    # first — both editable from the admin UI. Issuer CIKs come from the SEC's company_tickers.json (`instilens sec-ciks`).
    sec_form4_enabled: bool = True
    sec_form4_max_issuers: int = 200
    sec_form4_days_back: int = 120  # how far back the per-issuer filing listing is read

    # Auth & hardening. In production INSTILENS_ENVIRONMENT=production refuses the default secret.
    environment: str = "development"
    jwt_secret: str = "dev-only-change-me"
    jwt_ttl_minutes: int = 60 * 24 * 7
    allow_registration: bool = True  # open beta; flip off for invite-only
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "tauri://localhost", "http://tauri.localhost"]
    auth_rate_limit_per_minute: int = 10  # per client IP on /auth/login and /auth/register
    trusted_proxies: list[str] = ["127.0.0.1/32", "::1/128"]  # only these peers may set X-Forwarded-For (nginx)
    account_lockout_attempts: int = 8  # failed logins per e-mail within the lockout window → temporary lock
    account_lockout_minutes: int = 15
    breached_password_check: bool = True  # NIST 800-63B-4: reject passwords seen in breaches (HIBP k-anonymity)
    ai_requests_per_hour: int = 30  # per user: /research and forced AI refreshes (paid calls)

    # Desktop releases (Tauri). Installers live on this disk; CI/scripts upload with the key; the app's
    # updater verifies the minisign signature made with TAURI_SIGNING_PRIVATE_KEY at build time.
    releases_dir: Path = BACKEND_ROOT / "media" / "releases"
    release_upload_key: str | None = None  # openssl rand -hex 32
    desktop_updater_pubkey: str | None = None  # from `tauri signer generate` (public half; safe to publish)

    # Live TV widget: YouTube channel IDs that run 24/7 finance streams, "Name|CHANNEL_ID" comma-separated.
    # (Bloomberg HT blocks embedding — "izlemeyi engelledi" — so it is not in the default list.)
    live_tv_channels: str = "Ekotürk|UCAGVKxpAKwXMWdmcHbrvcwQ, TRT Haber|UCBgTP2LOFVPmq15W-RH-WXA, Yahoo Finance|UCEAZeUIeJs0IjQiqTCdVSIg, CNBC|UCvJJ_dzjViJCoLf5uKUTwoA, Bloomberg TV|UCdK2BueKxC9VxXh7e1Ne4oQ"

    # News ticker: RSS headlines (TR first, then global). Optional NewsAPI key adds a keyword source.
    news_enabled: bool = True
    newsapi_key: str | None = None
    ai_news_enabled: bool = True  # Claude tags/summaries for headlines (needs ANTHROPIC_API_KEY)
    ai_news_model: str = "claude-haiku-4-5"  # headline tagging is a cheap pass; ai_model (briefs/research) stays the strong one

    # Notification delivery. Telegram: create a bot with @BotFather, put the token here; users paste their chat id.
    telegram_bot_token: str | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: str | None = None
    smtp_from: str | None = None
    public_url: str = "http://localhost:5173"  # used in notification links
    # OneSignal (optional, alongside VAPID). App ID is public (frontend), REST key stays here.
    onesignal_app_id: str | None = None
    onesignal_rest_api_key: str | None = None
    # Web Push (VAPID). Generate once: `instilens vapid-keys`. Push needs HTTPS on the site.
    vapid_public_key: str | None = None
    vapid_private_key: str | None = None
    vapid_subject: str = "mailto:alerts@instilens.app"

    # Text-to-speech for AI notes (optional). ElevenLabs preferred for Turkish; OpenAI as alternative.
    elevenlabs_api_key: str | None = None
    elevenlabs_voice_id: str = "pFZP5JQG7iQjIQuC4Bku"  # fallback voice (Lily)
    # Voice settings that sounded best in Emre's other product (fal-app): stability 0.5, style 0.5, speaker boost.
    elevenlabs_model: str = "eleven_multilingual_v2"  # or eleven_v3 (most expressive), eleven_turbo_v2_5 (fast)
    elevenlabs_speed: float = 0.92  # 0.7–1.2; finance narration reads better slightly under 1.0
    elevenlabs_stability: float = 0.4  # lower = livelier intonation, higher = flatter and more consistent
    elevenlabs_style: float = 0.55  # 0 = flat, 1 = very expressive
    elevenlabs_speaker_boost: bool = True
    elevenlabs_paragraph_pause_s: float = 0.9  # breathing room between paragraphs
    # Per language × gender; empty = fall back to elevenlabs_voice_id. Pick Turkish voices in the library.
    elevenlabs_voice_tr_female: str = ""
    elevenlabs_voice_tr_male: str = ""
    elevenlabs_voice_en_female: str = "pFZP5JQG7iQjIQuC4Bku"  # Lily
    elevenlabs_voice_en_male: str = "pNInz6obpgDQGcFmaJgB"  # Adam (ElevenLabs premade)
    # Extra selectable voices, editable from Admin → Ayarlar: "tr:female:VOICE_ID:Ad, en:male:VOICE_ID:Name, ..."
    elevenlabs_extra_voices: str = ("tr:female:21m00Tcm4TlvDq8ikWAM:Rachel, tr:male:VR6AewLTigWG4xSOukaG:Arnold, tr:female:EXAVITQu4vr4xnSDxMaL:Bella, tr:male:TxGEqnHWrfWFTfGW9XjX:Josh, "
                                    "en:female:21m00Tcm4TlvDq8ikWAM:Rachel, en:male:VR6AewLTigWG4xSOukaG:Arnold, en:female:EXAVITQu4vr4xnSDxMaL:Bella, en:male:TxGEqnHWrfWFTfGW9XjX:Josh")
    openai_api_key: str | None = None
    openai_tts_voice: str = "alloy"

    # AI research engine. "claude" uses the Anthropic SDK (ANTHROPIC_API_KEY or `ant auth login`);
    # "local" is reserved for a self-hosted model (phase 3).
    ai_provider: str = "claude"
    # Read from backend/.env or the environment as ANTHROPIC_API_KEY (or INSTILENS_ANTHROPIC_API_KEY).
    anthropic_api_key: str | None = Field(None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "INSTILENS_ANTHROPIC_API_KEY"))
    ai_model: str = "claude-opus-5"  # briefs, stock notes, research
    ai_extract_model: str = "claude-haiku-4-5"  # KAP filing extraction: every number is validated against the source text, so the cheap model is enough
    ai_local_base_url: str = "http://127.0.0.1:11434"
    ai_local_model: str = ""


settings = Settings()
