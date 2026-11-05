import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import make_url

from app.core.config import get_settings
from app.services import database
from app.services.rate_limit import RateLimit, check_rate_limits

BACKEND_ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.integration
def test_postgresql_rate_limits_are_atomic_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    test_database_url = os.getenv("AUTH_TEST_DATABASE_URL")
    if not test_database_url:
        pytest.skip("Set AUTH_TEST_DATABASE_URL to a disposable PostgreSQL *_test database")
    current_database_url = get_settings().database_url
    test_database_name = make_url(test_database_url).database or ""
    if (
        test_database_url == current_database_url
        or not test_database_url.startswith("postgresql")
        or not test_database_name.endswith("_test")
    ):
        pytest.fail(
            "AUTH_TEST_DATABASE_URL must be a PostgreSQL database ending in _test "
            "and must not match DATABASE_URL"
        )

    monkeypatch.setenv("DATABASE_URL", test_database_url)
    monkeypatch.setenv("ENVIRONMENT", "test")
    monkeypatch.setenv("CSRF_SECRET_KEY", "test-only-csrf-secret-with-enough-bytes")
    database.get_session_factory.cache_clear()
    database.get_database_engine.cache_clear()
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(config, "head")

    identity = f"concurrent-{uuid4()}"
    limit = RateLimit("postgres-concurrency-test", identity, 12)
    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(lambda _: check_rate_limits([limit]), range(36)))

    assert sum(results) == 12
    database.get_database_engine().dispose()
    database.get_session_factory.cache_clear()
    database.get_database_engine.cache_clear()
