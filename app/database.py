"""Database engine and session management."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from app.config import get_settings

Base = declarative_base()

_engine = None
_SessionLocal = None


def get_engine():
    """Return the SQLAlchemy engine (created once)."""
    global _engine
    if _engine is None:
        settings = get_settings()
        _engine = create_engine(
            settings.database_url,
            connect_args={"check_same_thread": False}  # SQLite only
            if settings.database_url.startswith("sqlite")
            else {},
            echo=False,
        )
    return _engine


def get_session_factory():
    """Return the session factory (created once)."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(
            autocommit=False,
            autoflush=False,
            bind=get_engine(),
        )
    return _SessionLocal


def get_db():
    """Yield a database session for dependency injection."""
    SessionLocal = get_session_factory()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db():
    """Create all tables defined in models.

    Call this on startup if not using Alembic migrations.
    """
    from app import models  # noqa: F401 — ensure models are imported

    Base.metadata.create_all(bind=get_engine())
