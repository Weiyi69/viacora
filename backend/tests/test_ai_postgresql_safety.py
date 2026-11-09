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
            None,
            "localhost",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_production_test",
            None,
            "production-like",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_dev",
            None,
            "viacora_dev",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora",
            None,
            "end in _test",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_test?sslmode=require",
            None,
            "query parameters or a fragment",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_test#fragment",
            None,
            "query parameters or a fragment",
        ),
        (
            "postgresql+psycopg://user@localhost:0/viacora_test",
            None,
            "invalid port",
        ),
        (
            "postgresql+psycopg://user@localhost:65536/viacora_test",
            None,
            "invalid port",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_test",
            "postgresql+psycopg://other@127.0.0.1:5432/viacora_test",
            "differ from DATABASE_URL",
        ),
        (
            "postgresql+psycopg://user@localhost/viacora_test",
            "not a URL",
            "DATABASE_URL is malformed",
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


def test_ai_test_database_url_accepts_localhost_test_database() -> None:
    url = validate_ai_test_database_url(
        "postgresql+psycopg://test_role@localhost:5432/viacora_test",
        "postgresql+psycopg://dev_role@localhost:5432/viacora_dev",
    )

    assert url.drivername == "postgresql+psycopg"
    assert url.host == "localhost"
    assert url.database == "viacora_test"


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "[::1]"])
def test_ai_test_database_url_accepts_loopback_addresses(host: str) -> None:
    url = validate_ai_test_database_url(
        f"postgresql+psycopg://test_role@{host}/viacora_test",
        None,
    )

    assert url.database == "viacora_test"
