from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from instilens.config import BACKEND_ROOT
from instilens.domain.models import Base
from instilens.ingestion.kap.fixture_adapter import KapFixtureAdapter
from instilens.services import pipeline

FIXTURES = BACKEND_ROOT / "fixtures"
AS_OF = date(2026, 9, 14)


@pytest.fixture(autouse=True)
def _offline_password_policy():
    """Tests never call the HIBP breach API; the policy itself is covered in test_auth."""
    from instilens.api.hardening import reset_rate_limits
    from instilens.config import settings

    settings.breached_password_check = False
    reset_rate_limits()
    yield
    reset_rate_limits()


@pytest.fixture
def session() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool,  # TestClient runs in a thread
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()


@pytest.fixture
def pipeline_run(session: Session) -> dict[str, int]:
    """Runs the whole chain on the synthetic fixtures. This is the MVP acceptance chain."""
    result = pipeline.run_all(
        session, KapFixtureAdapter(FIXTURES / "kap"), AS_OF, prices_csv=FIXTURES / "prices_TR.csv"
    )
    session.commit()
    return result
