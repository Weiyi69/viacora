import hashlib
import hmac
import secrets
from functools import lru_cache
from urllib.parse import urlparse

from fastapi import HTTPException, Request, status

from app.core.config import get_settings

CSRF_HEADER = "X-CSRF-Token"


@lru_cache(maxsize=1)
def _development_secret() -> bytes:
    return secrets.token_bytes(32)


def _secret() -> bytes:
    configured = get_settings().csrf_secret_key
    if configured:
        return configured.encode("utf-8")
    if get_settings().environment == "production":
        raise RuntimeError("CSRF_SECRET_KEY must be configured")
    return _development_secret()


def _sign(value: str) -> str:
    return hmac.new(_secret(), value.encode("utf-8"), hashlib.sha256).hexdigest()


def new_double_submit_token() -> str:
    nonce = secrets.token_urlsafe(32)
    return f"{nonce}.{_sign(nonce)}"


def session_csrf_token(session_id: str) -> str:
    return _sign(f"session:{session_id}")


def _same_token(cookie_token: str | None, header_token: str | None) -> bool:
    return bool(
        cookie_token
        and header_token
        and hmac.compare_digest(cookie_token, header_token)
    )


def _valid_double_submit(token: str) -> bool:
    nonce, separator, signature = token.partition(".")
    return bool(
        separator
        and nonce
        and hmac.compare_digest(signature, _sign(nonce))
    )


def _trusted_origins() -> set[str]:
    from urllib.parse import urlparse

    settings = get_settings()
    return {
        f"{parsed.scheme}://{parsed.netloc}"
        for value in (settings.frontend_origin, settings.frontend_base_url)
        if (parsed := urlparse(value)).scheme and parsed.netloc
    }


def _request_origin(request: Request) -> str | None:
    origin = request.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    referer = request.headers.get("referer")
    if not referer:
        return None
    parsed = urlparse(referer)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def validate_request_origin(request: Request) -> None:
    if _request_origin(request) not in _trusted_origins():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Request origin is not allowed",
        )


def validate_csrf_token(
    request: Request,
    *,
    authenticated_session_id: str | None = None,
) -> None:
    validate_request_origin(request)
    settings = get_settings()
    cookie_token = request.cookies.get(settings.csrf_cookie_name)
    header_token = request.headers.get(CSRF_HEADER)
    if not _same_token(cookie_token, header_token):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
    if authenticated_session_id:
        expected = session_csrf_token(authenticated_session_id)
        if not hmac.compare_digest(cookie_token or "", expected):
            raise HTTPException(status_code=403, detail="Invalid CSRF token")
    elif not _valid_double_submit(cookie_token or ""):
        raise HTTPException(status_code=403, detail="Invalid CSRF token")
