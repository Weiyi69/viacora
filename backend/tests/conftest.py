from collections.abc import Generator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.api.v1.dependencies import database_session
from app.main import app
from app.services import database, rate_limit

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def auth_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[TestClient, None, None]:
    try:
        database.get_database_engine().dispose()
    except RuntimeError:
        pass
    database.get_session_factory.cache_clear()
    database.get_database_engine.cache_clear()
    database_path = tmp_path / "auth-tests.sqlite"
    database_url = f"sqlite:///{database_path.as_posix()}"
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("DATABASE_URL", database_url)
    monkeypatch.setenv("CSRF_SECRET_KEY", "test-only-csrf-secret-with-enough-bytes")
    monkeypatch.setenv("COOKIE_SECURE", "false")
    monkeypatch.setenv("FRONTEND_ORIGIN", "http://localhost:5173")
    monkeypatch.setenv("FRONTEND_BASE_URL", "http://localhost:5173")
    monkeypatch.setenv("EMAIL_OUTBOX_PATH", str(tmp_path / "outbox"))
    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SMTP_USERNAME", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    monkeypatch.setenv("SMTP_FROM_ADDRESS", "")

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, "head")

    engine = create_engine(database_url, connect_args={"check_same_thread": False})
    factory = sessionmaker(bind=engine, expire_on_commit=False)

    def override_session() -> Generator[Session, None, None]:
        session = factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[database_session] = override_session
    monkeypatch.setattr(rate_limit, "get_session_factory", lambda: factory)
    database.get_session_factory.cache_clear()
    database.get_database_engine.cache_clear()

    try:
        with TestClient(app) as client:
            yield client
    finally:
        app.dependency_overrides.clear()
        engine.dispose()
        try:
            database.get_database_engine().dispose()
        except RuntimeError:
            pass
        database.get_session_factory.cache_clear()
        database.get_database_engine.cache_clear()
        rate_limit._requests_since_cleanup = 0
