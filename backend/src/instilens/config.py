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

    # AI research engine. "claude" uses the Anthropic SDK (ANTHROPIC_API_KEY or `ant auth login`);
    # "local" is reserved for a self-hosted model (phase 3).
    ai_provider: str = "claude"
    # Read from backend/.env or the environment as ANTHROPIC_API_KEY (or INSTILENS_ANTHROPIC_API_KEY).
    anthropic_api_key: str | None = Field(None, validation_alias=AliasChoices("ANTHROPIC_API_KEY", "INSTILENS_ANTHROPIC_API_KEY"))
    ai_model: str = "claude-opus-5"
    ai_local_base_url: str = "http://127.0.0.1:11434"
    ai_local_model: str = ""


settings = Settings()
