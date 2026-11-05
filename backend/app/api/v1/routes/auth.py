from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import database_session, require_csrf
from app.core.config import get_settings
from app.models import User
from app.schemas.auth import (
    CsrfResponse,
    EmailRequest,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    PasswordResetRequest,
    RegisterRequest,
    SessionResponse,
    TokenRequest,
    UserResponse,
)
from app.security.csrf import new_double_submit_token, session_csrf_token
from app.services.auth import (
    GENERIC_RECOVERY_MESSAGE,
    authenticate_user,
    enforce_rate_limits,
    get_user_from_session,
    login_rate_limits,
    normalize_email,
    recovery_rate_limits,
    registration_rate_limits,
    register_user,
    request_password_reset,
    resend_verification,
    reset_password,
    revoke_session,
    token_rate_limits,
    verify_email,
)

router = APIRouter(prefix="/auth", tags=["authentication"])


def user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        email_verified=user.email_verified_at is not None,
    )


def _secure_cookie() -> bool:
    settings = get_settings()
    return settings.cookie_secure if settings.cookie_secure is not None else settings.environment != "development"


def _set_csrf_cookie(response: Response, token: str, *, max_age: int = 3600) -> None:
    settings = get_settings()
    response.set_cookie(
        key=settings.csrf_cookie_name,
        value=token,
        max_age=max_age,
        httponly=False,
        secure=_secure_cookie(),
        samesite=get_settings().cookie_samesite,
        path="/",
    )


def _set_session_cookies(response: Response, raw_session_id: str) -> None:
    settings = get_settings()
    session_age = int(timedelta(days=14).total_seconds())
    response.set_cookie(
        key=settings.session_cookie_name,
        value=raw_session_id,
        max_age=session_age,
        httponly=True,
        secure=_secure_cookie(),
        samesite=settings.cookie_samesite,
        path="/",
    )
    _set_csrf_cookie(response, session_csrf_token(raw_session_id), max_age=session_age)


def _clear_auth_cookies(response: Response) -> None:
    settings = get_settings()
    secure = _secure_cookie()
    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        secure=secure,
        httponly=True,
        samesite=settings.cookie_samesite,
    )
    response.delete_cookie(
        key=settings.csrf_cookie_name,
        path="/",
        secure=secure,
        httponly=False,
        samesite=settings.cookie_samesite,
    )


def _client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


@router.get("/csrf", response_model=CsrfResponse)
def get_csrf_token(request: Request, response: Response) -> CsrfResponse:
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    token = session_csrf_token(session_id) if session_id else new_double_submit_token()
    _set_csrf_cookie(response, token, max_age=14 * 24 * 60 * 60 if session_id else 3600)
    return CsrfResponse(csrf_token=token)


@router.post(
    "/register",
    response_model=MessageResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def register(
    payload: RegisterRequest,
    request: Request,
    session: Session = Depends(database_session),
) -> MessageResponse:
    email = normalize_email(str(payload.email))
    enforce_rate_limits(registration_rate_limits(email, _client_ip(request)))
    register_user(
        session,
        email=email,
        display_name=payload.display_name,
        password=payload.password,
    )
    return MessageResponse(message="Check your email for a verification link")


@router.post(
    "/login",
    response_model=LoginResponse,
    dependencies=[Depends(require_csrf)],
)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: Session = Depends(database_session),
) -> LoginResponse:
    email = normalize_email(str(payload.email))
    enforce_rate_limits(login_rate_limits(email, _client_ip(request)))
    user, raw_session_id = authenticate_user(
        session,
        email=email,
        password=payload.password,
        prior_session_id=request.cookies.get(get_settings().session_cookie_name),
    )
    _set_session_cookies(response, raw_session_id)
    return LoginResponse(user=user_response(user))


@router.post(
    "/logout",
    response_model=MessageResponse,
    dependencies=[Depends(require_csrf)],
)
def logout(
    request: Request,
    response: Response,
    session: Session = Depends(database_session),
) -> MessageResponse:
    revoke_session(
        session,
        request.cookies.get(get_settings().session_cookie_name),
    )
    _clear_auth_cookies(response)
    return MessageResponse(message="Logged out")


@router.get("/session", response_model=SessionResponse)
def get_session(
    request: Request,
    session: Session = Depends(database_session),
) -> SessionResponse:
    user = get_user_from_session(
        session,
        request.cookies.get(get_settings().session_cookie_name),
        require_verified=False,
    )
    return SessionResponse(
        authenticated=user is not None,
        user=user_response(user) if user else None,
    )


@router.post(
    "/email-verification/resend",
    response_model=MessageResponse,
    dependencies=[Depends(require_csrf)],
)
def resend_email_verification(
    payload: EmailRequest,
    request: Request,
    session: Session = Depends(database_session),
) -> MessageResponse:
    email = normalize_email(str(payload.email))
    enforce_rate_limits(recovery_rate_limits("verification-resend", email, _client_ip(request)))
    resend_verification(session, email)
    return MessageResponse(message=GENERIC_RECOVERY_MESSAGE)


@router.post(
    "/email-verification/confirm",
    response_model=MessageResponse,
    dependencies=[Depends(require_csrf)],
)
def confirm_email_verification(
    payload: TokenRequest,
    request: Request,
    session: Session = Depends(database_session),
) -> MessageResponse:
    enforce_rate_limits(token_rate_limits("verification-confirm", payload.token, _client_ip(request)))
    if not verify_email(session, payload.token):
        raise HTTPException(status_code=400, detail="Verification link is invalid or expired")
    return MessageResponse(message="Email verified. You can now sign in.")


@router.post(
    "/password/forgot",
    response_model=MessageResponse,
    dependencies=[Depends(require_csrf)],
)
def forgot_password(
    payload: EmailRequest,
    request: Request,
    session: Session = Depends(database_session),
) -> MessageResponse:
    email = normalize_email(str(payload.email))
    enforce_rate_limits(recovery_rate_limits("password-forgot", email, _client_ip(request)))
    request_password_reset(session, email)
    return MessageResponse(message=GENERIC_RECOVERY_MESSAGE)


@router.post(
    "/password/reset",
    response_model=MessageResponse,
    dependencies=[Depends(require_csrf)],
)
def reset_password_endpoint(
    payload: PasswordResetRequest,
    request: Request,
    response: Response,
    session: Session = Depends(database_session),
) -> MessageResponse:
    enforce_rate_limits(token_rate_limits("password-reset", payload.token, _client_ip(request)))
    if not reset_password(session, payload.token, payload.new_password):
        raise HTTPException(status_code=400, detail="Reset link is invalid or expired")
    _clear_auth_cookies(response)
    return MessageResponse(message="Password reset. You can now sign in.")
