from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.v1.dependencies import current_user, database_session, require_csrf
from app.models import User
from app.schemas.auth import ProfileUpdateRequest, UserResponse
from app.services.auth import update_profile

router = APIRouter(prefix="/users", tags=["users"])


def _user_response(user: User) -> UserResponse:
    return UserResponse(
        id=str(user.id),
        email=user.email,
        display_name=user.display_name,
        email_verified=user.email_verified_at is not None,
    )


@router.get("/me", response_model=UserResponse)
def get_current_user(user: User = Depends(current_user)) -> UserResponse:
    return _user_response(user)


@router.patch(
    "/me",
    response_model=UserResponse,
    dependencies=[Depends(require_csrf)],
)
def update_current_user(
    payload: ProfileUpdateRequest,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> UserResponse:
    return _user_response(update_profile(session, user, payload.display_name))
