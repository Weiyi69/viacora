from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import current_user, database_session, require_csrf
from app.models import User
from app.repositories.study_spaces import count_topics
from app.schemas.learning import (
    PageResponse,
    StudySpaceCreate,
    StudySpaceResponse,
    StudySpaceUpdate,
)
from app.services.study_spaces import (
    archive_study_space,
    create_study_space,
    delete_study_space,
    get_study_space,
    list_study_spaces,
    restore_study_space,
    update_study_space,
)

router = APIRouter(tags=["study spaces"])


def _study_space_response(study_space, topic_count: int = 0) -> StudySpaceResponse:
    return StudySpaceResponse.model_validate(study_space).model_copy(
        update={"topic_count": topic_count}
    )


@router.get(
    "/subjects/{subject_id}/study-spaces",
    response_model=PageResponse[StudySpaceResponse],
)
def get_subject_study_spaces(
    subject_id: UUID,
    q: str | None = Query(default=None, max_length=120),
    sort_by: str = Query(default="recent", pattern="^(recent|name)$"),
    include_archived: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> PageResponse[StudySpaceResponse]:
    study_spaces, total = list_study_spaces(
        session,
        user_id=user.id,
        subject_id=subject_id,
        query=q.strip() if q and q.strip() else None,
        sort_by=sort_by,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )
    return PageResponse[StudySpaceResponse](
        items=[
            _study_space_response(study_space, topic_count)
            for study_space, topic_count in study_spaces
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/subjects/{subject_id}/study-spaces",
    response_model=StudySpaceResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def post_study_space(
    subject_id: UUID,
    payload: StudySpaceCreate,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudySpaceResponse:
    study_space = create_study_space(
        session,
        user_id=user.id,
        subject_id=subject_id,
        payload=payload,
    )
    return _study_space_response(study_space)


@router.get("/study-spaces/{study_space_id}", response_model=StudySpaceResponse)
def get_study_space_detail(
    study_space_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudySpaceResponse:
    study_space = get_study_space(
        session, user_id=user.id, study_space_id=study_space_id
    )
    return _study_space_response(
        study_space,
        count_topics(session, study_space_id=study_space.id),
    )


@router.patch(
    "/study-spaces/{study_space_id}",
    response_model=StudySpaceResponse,
    dependencies=[Depends(require_csrf)],
)
def patch_study_space(
    study_space_id: UUID,
    payload: StudySpaceUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudySpaceResponse:
    study_space = update_study_space(
        session,
        user_id=user.id,
        study_space_id=study_space_id,
        payload=payload,
    )
    return _study_space_response(
        study_space,
        count_topics(session, study_space_id=study_space.id),
    )


@router.post(
    "/study-spaces/{study_space_id}/archive",
    response_model=StudySpaceResponse,
    dependencies=[Depends(require_csrf)],
)
def post_archive_study_space(
    study_space_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudySpaceResponse:
    study_space = archive_study_space(
        session, user_id=user.id, study_space_id=study_space_id
    )
    return _study_space_response(study_space)


@router.post(
    "/study-spaces/{study_space_id}/restore",
    response_model=StudySpaceResponse,
    dependencies=[Depends(require_csrf)],
)
def post_restore_study_space(
    study_space_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudySpaceResponse:
    study_space = restore_study_space(
        session, user_id=user.id, study_space_id=study_space_id
    )
    return _study_space_response(
        study_space,
        count_topics(session, study_space_id=study_space.id),
    )


@router.delete(
    "/study-spaces/{study_space_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def remove_study_space(
    study_space_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> Response:
    delete_study_space(session, user_id=user.id, study_space_id=study_space_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
