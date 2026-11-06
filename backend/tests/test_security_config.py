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


def test_ai_settings_are_disabled_by_default_and_fail_closed_when_enabled() -> None:
    settings = Settings(_env_file=None, environment="development")
    assert settings.ai_enabled is False
    assert settings.ai_daily_input_token_limit == 100_000
    assert str(settings.ai_daily_cost_limit_usd) == "0.50"

    with pytest.raises(ValidationError, match="OPENAI_API_KEY"):
        Settings(_env_file=None, ai_enabled=True)


def test_ai_enabled_requires_pricing_and_valid_bounds() -> None:
    with pytest.raises(ValidationError, match="AI_PRICING_VERSION"):
        Settings(
            _env_file=None,
            ai_enabled=True,
            openai_api_key="test-key",
        )
    with pytest.raises(ValidationError, match="overlap"):
        Settings(
            _env_file=None,
            ai_chunk_max_utf8_bytes=100,
            ai_chunk_target_utf8_bytes=80,
            ai_chunk_overlap_utf8_bytes=100,
        )
