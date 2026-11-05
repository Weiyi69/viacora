from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import EmailActionToken, User, UserSession
from app.repositories.users import get_user_by_email
from app.security.passwords import (
    PasswordHashCapacityError,
    hash_password,
    verify_password,
)
from app.security.tokens import new_token, token_digest
from app.services.email import OutgoingEmail, deliver_email
from app.services.rate_limit import (
    RateLimit,
    RateLimitUnavailable,
    check_rate_limits,
)

SESSION_LIFETIME = timedelta(days=14)
GENERIC_LOGIN_ERROR = "Email or password is incorrect"
GENERIC_RECOVERY_MESSAGE = "If the account can receive this email, instructions will be sent"


def normalize_email(email: str) -> str:
    return email.strip().lower()


def _client_ip(request_client_host: str | None) -> str:
    return request_client_host or "unknown"


def enforce_rate_limits(limits: list[RateLimit]) -> None:
    try:
        allowed = check_rate_limits(limits)
    except RateLimitUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication protection is temporarily unavailable",
        ) from error
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many attempts. Try again later.",
            headers={"Retry-After": str(get_settings().rate_limit_window_seconds)},
        )


def login_rate_limits(email: str, ip: str) -> list[RateLimit]:
    settings = get_settings()
    return [
        RateLimit("login-ip", ip, settings.rate_limit_login_ip),
        RateLimit("login-account", email, settings.rate_limit_login_account),
    ]


def registration_rate_limits(email: str, ip: str) -> list[RateLimit]:
    settings = get_settings()
    return [
        RateLimit("register-ip", ip, settings.rate_limit_register_ip),
        RateLimit("register-account", email, settings.rate_limit_recovery_account),
    ]


def recovery_rate_limits(action: str, email: str, ip: str) -> list[RateLimit]:
    settings = get_settings()
    return [
        RateLimit(f"{action}-ip", ip, settings.rate_limit_recovery_ip),
        RateLimit(f"{action}-account", email, settings.rate_limit_recovery_account),
    ]


def token_rate_limits(action: str, token: str, ip: str) -> list[RateLimit]:
    settings = get_settings()
    return [
        RateLimit(f"{action}-ip", ip, settings.rate_limit_recovery_ip),
        RateLimit(f"{action}-token", token_digest(token), settings.rate_limit_recovery_account),
    ]


def _token_link(path: str, token: str) -> str:
    base_url = get_settings().frontend_base_url.rstrip("/")
    return f"{base_url}{path}#token={quote(token)}"


def _send_verification(user: User, token: str) -> None:
    link = _token_link("/verify-email", token)
    deliver_email(
        OutgoingEmail(
            recipient=user.email,
            subject="Verify your Viacora email",
            body=f"Verify your email address by opening this link:\n\n{link}\n",
        )
    )


def _send_password_reset(user: User, token: str) -> None:
    link = _token_link("/reset-password", token)
    deliver_email(
        OutgoingEmail(
            recipient=user.email,
            subject="Reset your Viacora password",
            body=f"Reset your password by opening this link:\n\n{link}\n",
        )
    )


def register_user(
    session: Session,
    *,
    email: str,
    display_name: str,
    password: str,
) -> User:
    normalized_email = normalize_email(email)
    if get_user_by_email(session, normalized_email):
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unable to register with these details",
        )
    try:
        password_hash = hash_password(password)
    except PasswordHashCapacityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily busy",
        ) from error

    token = new_token()
    settings = get_settings()
    user = User(
        email=normalized_email,
        display_name=display_name.strip(),
        password_hash=password_hash,
        status="active",
    )
    try:
        session.add(user)
        session.flush()
        session.add(
            EmailActionToken(
                user_id=user.id,
                purpose="verification",
                token_hash=token_digest(token),
                expires_at=datetime.now(timezone.utc)
                + timedelta(hours=settings.verification_token_ttl_hours),
            )
        )
        session.commit()
    except IntegrityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Unable to register with these details",
        ) from error
    except SQLAlchemyError:
        session.rollback()
        raise

    _send_verification(user, token)
    return user


def authenticate_user(
    session: Session,
    *,
    email: str,
    password: str,
    prior_session_id: str | None,
) -> tuple[User, str]:
    user = get_user_by_email(session, normalize_email(email))
    try:
        valid_password = verify_password(
            password,
            user.password_hash if user else None,
        )
    except PasswordHashCapacityError as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily busy",
        ) from error
    if not user or not valid_password or user.status != "active":
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=GENERIC_LOGIN_ERROR,
        )

    raw_session_id = new_token()
    now = datetime.now(timezone.utc)
    if prior_session_id:
        session.execute(
            update(UserSession)
            .where(
                UserSession.session_hash == token_digest(prior_session_id),
                UserSession.revoked_at.is_(None),
            )
            .values(revoked_at=now)
        )
    session.add(
        UserSession(
            user_id=user.id,
            session_hash=token_digest(raw_session_id),
            expires_at=now + SESSION_LIFETIME,
        )
    )
    session.commit()
    return user, raw_session_id


def get_active_session(session: Session, raw_session_id: str | None) -> UserSession | None:
    if not raw_session_id:
        return None
    now = datetime.now(timezone.utc)
    return session.scalar(
        select(UserSession).where(
            UserSession.session_hash == token_digest(raw_session_id),
            UserSession.revoked_at.is_(None),
            UserSession.expires_at > now,
        )
    )


def get_user_from_session(
    session: Session, raw_session_id: str | None, *, require_verified: bool = True
) -> User | None:
    active_session = get_active_session(session, raw_session_id)
    if not active_session:
        return None
    user = session.get(User, active_session.user_id)
    if not user or user.status != "active":
        return None
    if require_verified and user.email_verified_at is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Email verification is required",
        )
    active_session.last_used_at = datetime.now(timezone.utc)
    session.commit()
    return user


def revoke_session(session: Session, raw_session_id: str | None) -> None:
    if not raw_session_id:
        return
    session.execute(
        update(UserSession)
        .where(
            UserSession.session_hash == token_digest(raw_session_id),
            UserSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.now(timezone.utc))
    )
    session.commit()


def resend_verification(session: Session, email: str) -> None:
    user = get_user_by_email(session, normalize_email(email))
    if not user or user.email_verified_at is not None or user.status != "active":
        session.rollback()
        return
    now = datetime.now(timezone.utc)
    token = new_token()
    session.execute(
        update(EmailActionToken)
        .where(
            EmailActionToken.user_id == user.id,
            EmailActionToken.purpose == "verification",
            EmailActionToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    session.add(
        EmailActionToken(
            user_id=user.id,
            purpose="verification",
            token_hash=token_digest(token),
            expires_at=now + timedelta(hours=get_settings().verification_token_ttl_hours),
        )
    )
    session.commit()
    _send_verification(user, token)


def verify_email(session: Session, token: str) -> bool:
    now = datetime.now(timezone.utc)
    user_id = session.execute(
        update(EmailActionToken)
        .where(
            EmailActionToken.token_hash == token_digest(token),
            EmailActionToken.purpose == "verification",
            EmailActionToken.consumed_at.is_(None),
            EmailActionToken.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(EmailActionToken.user_id)
    ).scalar_one_or_none()
    if user_id is None:
        session.rollback()
        return False
    user = session.get(User, user_id)
    if not user or user.status != "active":
        session.rollback()
        return False
    user.email_verified_at = now
    session.execute(
        update(EmailActionToken)
        .where(
            EmailActionToken.user_id == user_id,
            EmailActionToken.purpose == "verification",
            EmailActionToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    session.commit()
    return True


def request_password_reset(session: Session, email: str) -> None:
    user = get_user_by_email(session, normalize_email(email))
    if not user or user.status != "active":
        session.rollback()
        return
    now = datetime.now(timezone.utc)
    token = new_token()
    session.execute(
        update(EmailActionToken)
        .where(
            EmailActionToken.user_id == user.id,
            EmailActionToken.purpose == "password_reset",
            EmailActionToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    session.add(
        EmailActionToken(
            user_id=user.id,
            purpose="password_reset",
            token_hash=token_digest(token),
            expires_at=now
            + timedelta(minutes=get_settings().password_reset_token_ttl_minutes),
        )
    )
    session.commit()
    _send_password_reset(user, token)


def reset_password(session: Session, token: str, new_password: str) -> bool:
    try:
        password_hash = hash_password(new_password)
    except PasswordHashCapacityError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Authentication service is temporarily busy",
        ) from error
    now = datetime.now(timezone.utc)
    user_id = session.execute(
        update(EmailActionToken)
        .where(
            EmailActionToken.token_hash == token_digest(token),
            EmailActionToken.purpose == "password_reset",
            EmailActionToken.consumed_at.is_(None),
            EmailActionToken.expires_at > now,
        )
        .values(consumed_at=now)
        .returning(EmailActionToken.user_id)
    ).scalar_one_or_none()
    if user_id is None:
        session.rollback()
        return False
    user = session.get(User, user_id)
    if not user or user.status != "active":
        session.rollback()
        return False
    user.password_hash = password_hash
    session.execute(
        update(UserSession)
        .where(UserSession.user_id == user_id, UserSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    session.execute(
        update(EmailActionToken)
        .where(
            EmailActionToken.user_id == user_id,
            EmailActionToken.purpose == "password_reset",
            EmailActionToken.consumed_at.is_(None),
        )
        .values(consumed_at=now)
    )
    session.commit()
    return True


def update_profile(session: Session, user: User, display_name: str) -> User:
    user.display_name = display_name.strip()
    session.commit()
    session.refresh(user)
    return user
