from functools import lru_cache

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.core.config import get_settings


@lru_cache(maxsize=1)
def get_database_engine() -> Engine:
    database_url = get_settings().database_url
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    return create_engine(database_url, pool_pre_ping=True)


def check_database_connection() -> None:
    with get_database_engine().connect() as connection:
        connection.execute(text("SELECT 1"))
