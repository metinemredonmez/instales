from instilens.config import settings
from instilens.ingestion.base import SourceAdapter
from instilens.ingestion.kap.api_adapter import KapApiAdapter
from instilens.ingestion.kap.fixture_adapter import KapFixtureAdapter


def build_kap_adapter() -> SourceAdapter:
    if settings.kap_adapter == "public":
        from instilens.ingestion.kap.public_adapter import KapPublicAdapter

        return KapPublicAdapter(
            days_back=settings.kap_public_days_back, max_details=settings.kap_public_max_details,
            max_reports=settings.kap_public_max_reports, fund_codes=settings.kap_public_fund_codes or None,
        )
    if settings.kap_adapter == "api":
        if not settings.kap_api_auth_header and not (settings.kap_api_key and settings.kap_api_secret):
            raise RuntimeError("set INSTILENS_KAP_API_AUTH_HEADER (or KAP_API_KEY + KAP_API_SECRET) for the KAP API adapter")
        return KapApiAdapter(
            settings.kap_api_base_url, settings.kap_api_key or "", settings.kap_api_secret or "",
            rate_per_min=settings.kap_api_rate_per_min, max_calls=settings.kap_api_max_calls,
            auth_header=settings.kap_api_auth_header,
        )
    return KapFixtureAdapter(settings.kap_fixture_dir)
