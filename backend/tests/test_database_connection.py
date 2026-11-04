import pytest

from app.core.config import get_settings
from app.services.database import check_database_connection


@pytest.mark.integration
def test_postgresql_connection() -> None:
    if not get_settings().database_url:
        pytest.fail("Set DATABASE_URL in the project .env before integration tests")

    check_database_connection()
