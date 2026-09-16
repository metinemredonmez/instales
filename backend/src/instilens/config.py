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

    # SEC EDGAR (Global). Free; SEC requires an identifying User-Agent "AppName contact@email".
    sec_adapter: str = "edgar"  # live EDGAR (free); "fixture" only for tests
    sec_fixture_dir: Path = BACKEND_ROOT / "fixtures" / "sec"
    sec_user_agent: str = "InstiLens research@instilens.app"
    sec_ciks: list[str] = ["1067983", "1350694", "1037389"]  # Berkshire, Bridgewater, Renaissance — edit freely

    # Auth & hardening. In production INSTILENS_ENVIRONMENT=production refuses the default secret.
    environment: str = "development"
    jwt_secret: str = "dev-only-change-me"
    jwt_ttl_minutes: int = 60 * 24 * 7
    allow_registration: bool = True  # open beta; flip off for invite-only
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173", "tauri://localhost", "http://tauri.localhost"]
    auth_rate_limit_per_minute: int = 10  # per client IP on /auth/login and /auth/register

    # News ticker: RSS headlines (TR first, then global). Optional NewsAPI key adds a keyword source.
    news_enabled: bool = True
    newsapi_key: str | None = None
    ai_news_enabled: bool = True  # Claude tags/summaries for headlines (needs ANTHROPIC_API_KEY)
    ai_news_model: str = "claude-opus-5"  # set claude-haiku-4-5 for a cheaper tagging pass

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
    # Per language × gender; empty = fall back to elevenlabs_voice_id. Pick Turkish voices in the library.
    elevenlabs_voice_tr_female: str = ""
    elevenlabs_voice_tr_male: str = ""
    elevenlabs_voice_en_female: str = "pFZP5JQG7iQjIQuC4Bku"  # Lily
    elevenlabs_voice_en_male: str = "pNInz6obpgDQGcFmaJgB"  # Adam (ElevenLabs premade)
    openai_api_key: str | None = None
    openai_tts_voice: str = "alloy"

    # AI research engine. "claude" uses the Anthropic SDK (ANTHROPIC_API_KEY or `ant auth login`);
    # "local" is reserved for a self-hosted model (phase 3).
    ai_provider: str = "claude"
    # Read from backend/.env or the environment as ANTHROPIC_API_KEY (or INSTILENS_ANTHROPIC_API_KEY).
    anthropic_api_key: str | None = Field(None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "INSTILENS_ANTHROPIC_API_KEY"))
    ai_model: str = "claude-opus-5"
    ai_local_base_url: str = "http://127.0.0.1:11434"
    ai_local_model: str = ""


settings = Settings()
