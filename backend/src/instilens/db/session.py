from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from instilens.config import settings

_engine: Engine | None = None


def get_engine(url: str | None = None) -> Engine:
    global _engine
    if _engine is None or url is not None:
        _engine = create_engine(url or settings.database_url, future=True)
    return _engine


def init_db(engine: Engine | None = None) -> None:
    """Bring the database to the latest Alembic revision (tests use create_all directly)."""
    from alembic import command
    from alembic.config import Config

    from instilens.config import BACKEND_ROOT

    cfg = Config(str(BACKEND_ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    if engine is not None:
        cfg.set_main_option("sqlalchemy.url", str(engine.url).replace("%", "%%"))
    command.upgrade(cfg, "head")


@contextmanager
def session_scope(engine: Engine | None = None) -> Iterator[Session]:
    factory = sessionmaker(bind=engine or get_engine(), expire_on_commit=False)
    session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
