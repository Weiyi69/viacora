import pytest
from pydantic import ValidationError

from app.core.config import Settings


def test_development_settings_do_not_require_smtp_or_secrets() -> None:
    settings = Settings(_env_file=None, environment="development")

    assert settings.smtp_host is None
    assert settings.csrf_secret_key is None


def test_production_requires_https_secrets_and_smtp() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql+psycopg://localhost/viacora",
            csrf_secret_key="not-long-enough",
            frontend_origin="http://learn.example",
            frontend_base_url="http://learn.example",
        )

    settings = Settings(
        _env_file=None,
        environment="production",
        database_url="postgresql+psycopg://localhost/viacora",
        csrf_secret_key="test-production-key-with-more-than-32-bytes",
        frontend_origin="https://learn.example",
        frontend_base_url="https://learn.example",
        smtp_host="smtp.example",
        smtp_username="mailer",
        smtp_password="not-a-real-password",
        smtp_from_address="noreply@example.com",
    )
    assert settings.cookie_secure is None
    assert settings.cookie_samesite == "lax"


def test_production_rejects_plaintext_smtp_and_insecure_samesite() -> None:
    with pytest.raises(ValidationError):
        Settings(
            _env_file=None,
            environment="production",
            database_url="postgresql+psycopg://localhost/viacora",
            csrf_secret_key="test-production-key-with-more-than-32-bytes",
            frontend_origin="https://learn.example",
            frontend_base_url="https://learn.example",
            smtp_host="smtp.example",
            smtp_username="mailer",
            smtp_password="not-a-real-password",
            smtp_from_address="noreply@example.com",
            smtp_starttls=False,
        )
