"""Database engine and session management."""


from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from eap.config import get_settings

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine(db_url: str | None = None) -> Engine:
    global _engine
    if db_url is None:
        settings = get_settings()
        if not settings.database_url:
            raise ValueError("DATABASE_URL is not configured")
        db_url = settings.database_url

    if _engine is None:
        _engine = create_engine(db_url, pool_pre_ping=True)
    return _engine


def get_session_factory(db_url: str | None = None) -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        engine = get_engine(db_url)
        _session_factory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    return _session_factory
