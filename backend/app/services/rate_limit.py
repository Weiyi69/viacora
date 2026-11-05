import hashlib
import hmac
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from time import time

from sqlalchemy import delete, select, tuple_
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuthRateLimit, EmailActionToken, UserSession
from app.services.database import get_session_factory
from app.security.csrf import _secret

logger = logging.getLogger(__name__)
_cleanup_lock = threading.Lock()
_requests_since_cleanup = 0


class RateLimitUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RateLimit:
    scope: str
    identity: str
    maximum: int


def _hashed_key(scope: str, identity: str) -> str:
    message = f"{scope}:{identity}".encode("utf-8")
    return hmac.new(_secret(), message, hashlib.sha256).hexdigest()


def _next_cleanup_due() -> bool:
    global _requests_since_cleanup
    with _cleanup_lock:
        _requests_since_cleanup += 1
        if _requests_since_cleanup >= 128:
            _requests_since_cleanup = 0
            return True
        return False


def _increment(session: Session, key_hash: str, window_start: datetime, expires_at: datetime) -> int:
    dialect = session.bind.dialect.name if session.bind else ""
    insert = {
        "key_hash": key_hash,
        "window_start": window_start,
        "request_count": 1,
        "expires_at": expires_at,
        "updated_at": datetime.now(timezone.utc),
    }
    if dialect == "postgresql":
        statement = postgresql_insert(AuthRateLimit).values(**insert)
    elif dialect == "sqlite":
        statement = sqlite_insert(AuthRateLimit).values(**insert)
    else:
        raise RateLimitUnavailable("Unsupported database for atomic rate limiting")
    statement = statement.on_conflict_do_update(
        index_elements=[AuthRateLimit.key_hash, AuthRateLimit.window_start],
        set_={
            "request_count": AuthRateLimit.request_count + 1,
            "expires_at": expires_at,
            "updated_at": datetime.now(timezone.utc),
        },
    ).returning(AuthRateLimit.request_count)
    count = session.execute(statement).scalar_one()
    return int(count)


def _cleanup_expired(session: Session, cleanup_time: datetime) -> None:
    rate_limit_keys = select(AuthRateLimit.key_hash, AuthRateLimit.window_start).where(
        AuthRateLimit.expires_at <= cleanup_time
    ).limit(500)
    session.execute(
        delete(AuthRateLimit).where(
            tuple_(AuthRateLimit.key_hash, AuthRateLimit.window_start).in_(rate_limit_keys)
        )
    )
    expired_session_ids = select(UserSession.id).where(
        UserSession.expires_at <= cleanup_time
    ).limit(500)
    session.execute(
        delete(UserSession).where(UserSession.id.in_(expired_session_ids))
    )
    expired_token_ids = select(EmailActionToken.id).where(
        EmailActionToken.expires_at <= cleanup_time
    ).limit(500)
    session.execute(
        delete(EmailActionToken).where(EmailActionToken.id.in_(expired_token_ids))
    )


def check_rate_limits(limits: list[RateLimit]) -> bool:
    settings = get_settings()
    now_epoch = int(time())
    window_seconds = settings.rate_limit_window_seconds
    window_epoch = now_epoch - (now_epoch % window_seconds)
    window_start = datetime.fromtimestamp(window_epoch, timezone.utc)
    expires_at = window_start + timedelta(seconds=window_seconds)
    session: Session | None = None
    try:
        session = get_session_factory()()
        with session.begin():
            if _next_cleanup_due():
                _cleanup_expired(session, datetime.now(timezone.utc))
            allowed = True
            for limit in limits:
                count = _increment(
                    session,
                    _hashed_key(limit.scope, limit.identity),
                    window_start,
                    expires_at,
                )
                allowed = allowed and count <= limit.maximum
        return allowed
    except Exception as error:
        logger.exception("Authentication rate-limit storage failed")
        raise RateLimitUnavailable("Authentication protection is unavailable") from error
    finally:
        if session is not None:
            session.close()
