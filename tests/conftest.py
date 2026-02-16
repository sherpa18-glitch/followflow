"""Shared test fixtures for FollowFlow."""

import os
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.database import Base
from app import models  # noqa: F401 — register models with Base


@pytest.fixture(scope="function")
def db_session():
    """Create a fresh in-memory SQLite database for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture(autouse=True)
def mock_env(monkeypatch):
    """Set required environment variables for tests."""
    monkeypatch.setenv("INSTAGRAM_USERNAME", "test_user")
    monkeypatch.setenv("INSTAGRAM_PASSWORD", "test_pass")
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456:ABC-DEF")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "987654321")
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
