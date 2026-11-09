import pytest

from postgresql_test_safety import validate_ai_test_database_url


@pytest.mark.parametrize(
    ("test_url", "configured_url", "message"),
    [
        (None, None, "explicitly configured"),
        ("not a URL", None, "malformed"),
        ("mysql+pymysql://user@localhost/viacora_test", None, "driver"),
        (
            "postgresql+psycopg://user@db.example.com/viacora_test",
            "postgresql+psycopg://user@localhost/viacora_dev",
            "localhost",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_production_test",
            None,
            "production-like",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_dev",
            "postgresql+psycopg://user@localhost/viacora",
            "viacora_dev",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora",
            "postgresql+psycopg://user@localhost/viacora",
            "viacora_isolated_test",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_test",
            "postgresql+psycopg://user@localhost/viacora_dev",
            "viacora_test",
        ),
        (
            "postgresql+psycopg://user@localhost/other_test",
            "postgresql+psycopg://user@localhost/viacora_dev",
            "viacora_isolated_test",
        ),
        (
            "postgresql+psycopg://user@localhost:5433/viacora_isolated_test",
            "postgresql+psycopg://user@localhost/viacora_dev",
            "port 5432",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_isolated_test?sslmode=require",
            "postgresql+psycopg://user@localhost/viacora_dev",
            "query parameters or a fragment",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_isolated_test#fragment",
            "postgresql+psycopg://user@localhost/viacora_dev",
            "query parameters or a fragment",
        ),
        (
            "postgresql+psycopg://user@localhost:0/viacora_isolated_test",
            "postgresql+psycopg://user@localhost/viacora_dev",
            "invalid port",
        ),
        (
            "postgresql+psycopg://user@localhost:65536/viacora_isolated_test",
            "postgresql+psycopg://user@localhost/viacora_dev",
            "invalid port",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_isolated_test",
            "postgresql+psycopg://other@127.0.0.1:5432/viacora_isolated_test",
            "differ from DATABASE_URL",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_isolated_test",
            "not a URL",
            "DATABASE_URL is malformed",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_isolated_test",
            None,
            "DATABASE_URL must be explicitly configured",
        ),
    ],
)
def test_ai_test_database_url_rejects_unsafe_targets(
    test_url: str | None,
    configured_url: str | None,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        validate_ai_test_database_url(test_url, configured_url)


def test_ai_test_database_url_accepts_only_isolated_test_database() -> None:
    url = validate_ai_test_database_url(
        "postgresql+psycopg://test_role@localhost:5432/viacora_isolated_test",
        "postgresql+psycopg://dev_role@localhost:5432/viacora_dev",
    )

    assert url.drivername == "postgresql+psycopg"
    assert url.host == "localhost"
    assert url.port == 5432
    assert url.database == "viacora_isolated_test"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.0.0.2", "[::1]"])
def test_ai_test_database_url_accepts_loopback_addresses(host: str) -> None:
    url = validate_ai_test_database_url(
        f"postgresql+psycopg://test_role@{host}/viacora_isolated_test",
        "postgresql+psycopg://dev_role@localhost/viacora_dev",
    )

    assert url.database == "viacora_isolated_test"


def test_ai_test_database_url_rejects_same_normalized_database_url_target() -> None:
    with pytest.raises(ValueError, match="differ from DATABASE_URL"):
        validate_ai_test_database_url(
            "postgresql+psycopg://test_role@localhost:5432/viacora_isolated_test",
            "postgresql://dev_role@127.0.0.1/viacora_isolated_test",
        )
