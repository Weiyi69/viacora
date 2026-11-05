from collections.abc import Generator

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import User
from app.security.csrf import validate_csrf_token
from app.services.auth import get_user_from_session
from app.services.database import get_db_session


def database_session() -> Generator[Session, None, None]:
    yield from get_db_session()


def require_csrf(request: Request) -> None:
    settings = get_settings()
    session_id = request.cookies.get(settings.session_cookie_name)
    validate_csrf_token(request, authenticated_session_id=session_id)


def current_user(
    request: Request,
    session: Session = Depends(database_session),
) -> User:
    session_id = request.cookies.get(get_settings().session_cookie_name)
    user = get_user_from_session(session, session_id)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )
    return user
