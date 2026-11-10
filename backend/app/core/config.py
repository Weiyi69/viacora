from decimal import Decimal
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, model_validator
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
    document_storage_path: Path = PROJECT_ROOT / "backend" / ".local-storage"
    document_max_upload_bytes: int = 25 * 1024 * 1024
    document_max_request_bytes: int = 26 * 1024 * 1024
    document_storage_quota_bytes: int = 500 * 1024 * 1024
    document_uploads_per_hour: int = 10
    document_max_pages: int = 500
    document_max_extracted_text_bytes: int = 10 * 1024 * 1024
    document_worker_lease_seconds: int = 300
    document_retry_base_seconds: int = 60
    document_retry_max_seconds: int = 3600
    ai_enabled: bool = False
    openai_api_key: SecretStr | None = None
    ai_embedding_model: str = "text-embedding-3-small"
    ai_embedding_profile_id: str = "openai-text-embedding-3-small-1536-v1"
    ai_embedding_dimensions: int = 1536
    ai_structured_output_model: str = "gpt-4o-mini"
    ai_provider_timeout_seconds: float = Field(default=30.0, gt=0, le=120)
    ai_provider_max_retries: int = Field(default=2, ge=0, le=5)
    ai_provider_retry_base_seconds: float = Field(default=1.0, gt=0, le=30)
    ai_provider_retry_max_seconds: float = Field(default=10.0, gt=0, le=120)
    ai_max_request_bytes: int = Field(default=8192, gt=0, le=8192)
    ai_query_max_chars: int = Field(default=2048, gt=0, le=2048)
    ai_chunk_target_utf8_bytes: int = Field(default=800, gt=0, le=1024)
    ai_chunk_max_utf8_bytes: int = Field(default=1024, gt=0, le=1024)
    ai_chunk_overlap_utf8_bytes: int = Field(default=100, ge=0, le=1023)
    ai_embedding_batch_max_chunks: int = Field(default=16, gt=0, le=16)
    ai_embedding_batch_max_utf8_bytes: int = Field(default=16384, gt=0, le=16384)
    ai_retrieval_default_top_k: int = Field(default=5, gt=0, le=8)
    ai_retrieval_max_top_k: int = Field(default=8, gt=0, le=8)
    ai_retrieval_min_similarity: float = Field(default=0.2, ge=0, le=1)
    ai_daily_input_token_limit: int = Field(default=100_000, gt=0)
    ai_daily_cost_limit_usd: Decimal = Field(default=Decimal("0.50"), gt=0)
    study_material_enqueue_limit: int = Field(default=5, gt=0, le=100)
    study_material_max_request_bytes: int = Field(default=2048, gt=0, le=2048)
    ai_pricing_version: str | None = None
    ai_embedding_input_usd_per_million: Decimal | None = Field(default=None, gt=0)
    ai_structured_input_usd_per_million: Decimal | None = Field(default=None, gt=0)
    ai_structured_output_usd_per_million: Decimal | None = Field(default=None, gt=0)
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
        document_limits = (
            self.document_max_upload_bytes,
            self.document_max_request_bytes,
            self.document_storage_quota_bytes,
            self.document_uploads_per_hour,
            self.document_max_pages,
            self.document_max_extracted_text_bytes,
            self.document_worker_lease_seconds,
            self.document_retry_base_seconds,
            self.document_retry_max_seconds,
        )
        if any(limit < 1 for limit in document_limits):
            raise ValueError("Document processing limits must be positive")
        if self.document_max_request_bytes <= self.document_max_upload_bytes:
            raise ValueError("DOCUMENT_MAX_REQUEST_BYTES must exceed DOCUMENT_MAX_UPLOAD_BYTES")
        if self.document_max_upload_bytes > 25 * 1024 * 1024:
            raise ValueError("DOCUMENT_MAX_UPLOAD_BYTES cannot exceed 25 MiB")
        if self.document_max_pages > 500:
            raise ValueError("DOCUMENT_MAX_PAGES cannot exceed 500")
        if self.document_max_extracted_text_bytes > 10 * 1024 * 1024:
            raise ValueError("DOCUMENT_MAX_EXTRACTED_TEXT_BYTES cannot exceed 10 MiB")
        if self.document_retry_max_seconds < self.document_retry_base_seconds:
            raise ValueError("Document retry maximum must not be less than its base")
        if self.ai_provider_retry_max_seconds < self.ai_provider_retry_base_seconds:
            raise ValueError("AI retry maximum must not be less than its base")
        if self.ai_chunk_overlap_utf8_bytes >= self.ai_chunk_max_utf8_bytes:
            raise ValueError("AI chunk overlap must be smaller than the maximum byte size")
        if self.ai_chunk_target_utf8_bytes > self.ai_chunk_max_utf8_bytes:
            raise ValueError("AI chunk target must not exceed the maximum byte size")
        if self.ai_retrieval_default_top_k > self.ai_retrieval_max_top_k:
            raise ValueError("AI retrieval default top-k must not exceed its maximum")
        if self.ai_embedding_dimensions != 1536:
            raise ValueError("The initial AI embedding profile requires 1536 dimensions")
        if self.ai_enabled:
            if not self.openai_api_key or not self.openai_api_key.get_secret_value():
                raise ValueError("OPENAI_API_KEY is required when AI_ENABLED is true")
            if not self.ai_pricing_version:
                raise ValueError("AI_PRICING_VERSION is required when AI_ENABLED is true")
            required_rates = (
                self.ai_embedding_input_usd_per_million,
                self.ai_structured_input_usd_per_million,
                self.ai_structured_output_usd_per_million,
            )
            if any(rate is None for rate in required_rates):
                raise ValueError("AI pricing rates are required when AI_ENABLED is true")
        return self


def get_settings() -> Settings:
    return Settings()
