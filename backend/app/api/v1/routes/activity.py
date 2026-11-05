from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import current_user, database_session, require_csrf
from app.models import User
from app.schemas.learning import (
    ActivityAccessRequest,
    ActivityResponse,
    PageResponse,
)
from app.services.activity import list_activity, record_access

router = APIRouter(prefix="/activity", tags=["learning activity"])


@router.get("", response_model=PageResponse[ActivityResponse])
def get_activity(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> PageResponse[ActivityResponse]:
    activities, total = list_activity(
        session, user_id=user.id, page=page, page_size=page_size
    )
    return PageResponse[ActivityResponse](
        items=[ActivityResponse.model_validate(item) for item in activities],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/access",
    response_model=ActivityResponse,
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(require_csrf)],
)
def post_access(
    payload: ActivityAccessRequest,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> ActivityResponse:
    return ActivityResponse.model_validate(
        record_access(session, user_id=user.id, payload=payload)
    )
