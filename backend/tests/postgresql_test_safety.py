import ipaddress
import re

from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

_TEST_DATABASE_NAME = "viacora_isolated_test"
_PRODUCTION_NAME = re.compile(r"(^|[_-])(prod|production|live)([_-]|$)", re.IGNORECASE)


def _parse_postgresql_url(
    value: str, *, label: str, require_psycopg_driver: bool = True
) -> URL:
    if "#" in value:
        raise ValueError(f"{label} must not contain query parameters or a fragment")
    try:
        url = make_url(value)
    except (ArgumentError, TypeError, ValueError) as error:
        raise ValueError(f"{label} is malformed") from error
    supported_drivers = {"postgresql+psycopg"}
    if not require_psycopg_driver:
        supported_drivers.add("postgresql")
    if url.drivername not in supported_drivers:
        raise ValueError(f"{label} must use the postgresql+psycopg driver")
    if url.query:
        raise ValueError(f"{label} must not contain query parameters or a fragment")
    if not url.host or not url.database:
        raise ValueError(f"{label} must specify a host and database")
    if url.port is not None and not 1 <= url.port <= 65535:
        raise ValueError(f"{label} has an invalid port")
    return url


def _normalized_host(host: str) -> str:
    normalized = host.lower().strip("[]")
    if normalized == "localhost":
        return "localhost"
    try:
        if ipaddress.ip_address(normalized).is_loopback:
            return "localhost"
    except ValueError:
        pass
    return normalized


def _target(url: URL) -> tuple[str, int, str]:
    return (_normalized_host(url.host or ""), url.port or 5432, url.database or "")


def validate_ai_test_database_url(
    test_database_url: str | None,
    configured_database_url: str | None,
) -> URL:
    if not test_database_url or not test_database_url.strip():
        raise ValueError("AI_TEST_DATABASE_URL must be explicitly configured")

    test_url = _parse_postgresql_url(test_database_url.strip(), label="AI_TEST_DATABASE_URL")
    host = _normalized_host(test_url.host or "")
    database_name = test_url.database or ""

    if host != "localhost":
        raise ValueError("AI_TEST_DATABASE_URL must target localhost, 127.0.0.1, or ::1")
    if (test_url.port or 5432) != 5432:
        raise ValueError("AI_TEST_DATABASE_URL must use port 5432")
    if database_name.lower() == "viacora_dev":
        raise ValueError("AI_TEST_DATABASE_URL must not target viacora_dev")
    if database_name.lower() == "viacora_test":
        raise ValueError("AI_TEST_DATABASE_URL must not target viacora_test")
    if _PRODUCTION_NAME.search(database_name) or (
        test_url.username and _PRODUCTION_NAME.search(test_url.username)
    ):
        raise ValueError("AI_TEST_DATABASE_URL must not use a production-like name")
    if database_name != _TEST_DATABASE_NAME:
        raise ValueError(
            f"AI_TEST_DATABASE_URL must target {_TEST_DATABASE_NAME}"
        )

    if not configured_database_url or not configured_database_url.strip():
        raise ValueError("DATABASE_URL must be explicitly configured")
    configured_url = _parse_postgresql_url(
        configured_database_url.strip(),
        label="DATABASE_URL",
        require_psycopg_driver=False,
    )
    if _target(test_url) == _target(configured_url):
        raise ValueError("AI_TEST_DATABASE_URL must differ from DATABASE_URL")

    return test_url
