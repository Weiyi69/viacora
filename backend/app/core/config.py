from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    environment: Literal["development", "production", "test"] = "development"
    cookie_samesite: Literal["lax", "none"] = "lax"
    database_url: str | None = None
    frontend_origin: str = "http://localhost:5173"
    frontend_base_url: str = "http://localhost:5173"
    csrf_secret_key: str | None = None
    cookie_secure: bool | None = None
    session_cookie_name: str = "viacora_session"
    csrf_cookie_name: str = "viacora_csrf"
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: str | None = None
    smtp_from_address: str | None = None
    smtp_starttls: bool = True
    email_outbox_path: Path = PROJECT_ROOT / "backend" / ".local-outbox"
    verification_token_ttl_hours: int = 24
    password_reset_token_ttl_minutes: int = 60
    password_min_length: int = 12
    password_max_length: int = 128
    argon2_max_concurrent: int = 4
    rate_limit_window_seconds: int = 900
    rate_limit_login_ip: int = 20
    rate_limit_login_account: int = 10
    rate_limit_register_ip: int = 5
    rate_limit_recovery_ip: int = 10
    rate_limit_recovery_account: int = 5

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / "backend" / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @model_validator(mode="after")
    def validate_security_configuration(self) -> "Settings":
        smtp_values = (
            self.smtp_host,
            self.smtp_username,
            self.smtp_password,
            self.smtp_from_address,
        )
        if any(smtp_values) and not all(smtp_values):
            raise ValueError("SMTP host, username, password, and from address are required together")
        if self.smtp_host and not self.smtp_starttls:
            raise ValueError("SMTP must use verified STARTTLS")
        if self.password_min_length < 1 or self.password_max_length < self.password_min_length:
            raise ValueError("Password length limits are invalid")
        if self.argon2_max_concurrent < 1:
            raise ValueError("ARGON2_MAX_CONCURRENT must be positive")
        if self.rate_limit_window_seconds < 1:
            raise ValueError("RATE_LIMIT_WINDOW_SECONDS must be positive")
        if self.environment == "production":
            if not self.database_url:
                raise ValueError("DATABASE_URL is required in production")
            if not self.smtp_host:
                raise ValueError("SMTP configuration is required in production")
            if not self.csrf_secret_key or len(self.csrf_secret_key.encode()) < 32:
                raise ValueError("Production CSRF_SECRET_KEY must contain at least 32 bytes")
            if self.cookie_secure is False:
                raise ValueError("Production cookies must be Secure")
            if not self.frontend_base_url.startswith("https://") or not self.frontend_origin.startswith(
                "https://"
            ):
                raise ValueError("Production frontend URLs must use HTTPS")
        secure_cookies = (
            self.cookie_secure
            if self.cookie_secure is not None
            else self.environment != "development"
        )
        if self.cookie_samesite == "none" and not secure_cookies:
            raise ValueError("SameSite=None cookies must be Secure")
        if self.verification_token_ttl_hours < 1:
            raise ValueError("VERIFICATION_TOKEN_TTL_HOURS must be positive")
        if self.password_reset_token_ttl_minutes < 1:
            raise ValueError("PASSWORD_RESET_TOKEN_TTL_MINUTES must be positive")
        if self.password_max_length > 128:
            raise ValueError("PASSWORD_MAX_LENGTH cannot exceed 128")
        if not 1 <= self.smtp_port <= 65535:
            raise ValueError("SMTP_PORT must be between 1 and 65535")
        rate_limits = (
            self.rate_limit_login_ip,
            self.rate_limit_login_account,
            self.rate_limit_register_ip,
            self.rate_limit_recovery_ip,
            self.rate_limit_recovery_account,
        )
        if any(limit < 1 for limit in rate_limits):
            raise ValueError("Authentication rate limits must be positive")
        return self


def get_settings() -> Settings:
    return Settings()
