from instilens.config import settings
from instilens.ingestion.base import SourceAdapter
from instilens.ingestion.sec.edgar_adapter import SecEdgarAdapter
from instilens.ingestion.sec.fixture_adapter import SecFixtureAdapter


def build_sec_adapter() -> SourceAdapter:
    if settings.sec_adapter == "edgar":
        return SecEdgarAdapter(settings.sec_user_agent, settings.sec_ciks)
    return SecFixtureAdapter(settings.sec_fixture_dir)
