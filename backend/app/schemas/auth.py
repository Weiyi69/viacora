from pydantic import BaseModel, EmailStr, Field, field_validator

from app.core.config import get_settings


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(max_length=128)
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("password")
    @classmethod
    def validate_password_length(cls, value: str) -> str:
        settings = get_settings()
        if not settings.password_min_length <= len(value) <= settings.password_max_length:
            raise ValueError(
                f"Password must be between {settings.password_min_length} and "
                f"{settings.password_max_length} characters"
            )
        return value

    @field_validator("display_name")
    @classmethod
    def trim_display_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Display name cannot be blank")
        return cleaned


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class EmailRequest(BaseModel):
    email: EmailStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenRequest(BaseModel):
    token: str = Field(min_length=20, max_length=256)


class PasswordResetRequest(TokenRequest):
    new_password: str = Field(max_length=128)

    @field_validator("new_password")
    @classmethod
    def validate_password_length(cls, value: str) -> str:
        settings = get_settings()
        if not settings.password_min_length <= len(value) <= settings.password_max_length:
            raise ValueError(
                f"Password must be between {settings.password_min_length} and "
                f"{settings.password_max_length} characters"
            )
        return value


class UserResponse(BaseModel):
    id: str
    email: EmailStr
    display_name: str
    email_verified: bool


class LoginResponse(BaseModel):
    user: UserResponse


class SessionResponse(BaseModel):
    authenticated: bool
    user: UserResponse | None = None


class CsrfResponse(BaseModel):
    csrf_token: str


class MessageResponse(BaseModel):
    message: str


class ProfileUpdateRequest(BaseModel):
    display_name: str = Field(min_length=1, max_length=80)

    @field_validator("display_name")
    @classmethod
    def trim_display_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Display name cannot be blank")
        return cleaned
