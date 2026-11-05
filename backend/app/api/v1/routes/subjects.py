from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import current_user, database_session, require_csrf
from app.models import User
from app.repositories.subjects import count_active_study_spaces
from app.schemas.learning import PageResponse, SubjectCreate, SubjectResponse, SubjectUpdate
from app.services.subjects import (
    archive_subject,
    create_subject,
    delete_subject,
    get_subject,
    list_subjects,
    restore_subject,
    update_subject,
)

router = APIRouter(prefix="/subjects", tags=["subjects"])


def _subject_response(subject, study_space_count: int = 0) -> SubjectResponse:
    return SubjectResponse.model_validate(subject).model_copy(
        update={"study_space_count": study_space_count}
    )


@router.get("", response_model=PageResponse[SubjectResponse])
def get_subjects(
    q: str | None = Query(default=None, max_length=120),
    include_archived: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> PageResponse[SubjectResponse]:
    subjects, total = list_subjects(
        session,
        user_id=user.id,
        query=q.strip() if q and q.strip() else None,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )
    return PageResponse[SubjectResponse](
        items=[_subject_response(subject, count) for subject, count in subjects],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "",
    response_model=SubjectResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def post_subject(
    payload: SubjectCreate,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> SubjectResponse:
    subject = create_subject(session, user_id=user.id, payload=payload)
    return _subject_response(subject)


@router.get("/{subject_id}", response_model=SubjectResponse)
def get_subject_detail(
    subject_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> SubjectResponse:
    subject = get_subject(session, user_id=user.id, subject_id=subject_id)
    return _subject_response(
        subject,
        count_active_study_spaces(session, subject_id=subject.id),
    )


@router.patch(
    "/{subject_id}",
    response_model=SubjectResponse,
    dependencies=[Depends(require_csrf)],
)
def patch_subject(
    subject_id: UUID,
    payload: SubjectUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> SubjectResponse:
    subject = update_subject(
        session, user_id=user.id, subject_id=subject_id, payload=payload
    )
    return _subject_response(
        subject,
        count_active_study_spaces(session, subject_id=subject.id),
    )


@router.post(
    "/{subject_id}/archive",
    response_model=SubjectResponse,
    dependencies=[Depends(require_csrf)],
)
def post_archive_subject(
    subject_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> SubjectResponse:
    subject = archive_subject(session, user_id=user.id, subject_id=subject_id)
    return _subject_response(subject)


@router.post(
    "/{subject_id}/restore",
    response_model=SubjectResponse,
    dependencies=[Depends(require_csrf)],
)
def post_restore_subject(
    subject_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> SubjectResponse:
    subject = restore_subject(session, user_id=user.id, subject_id=subject_id)
    return _subject_response(
        subject,
        count_active_study_spaces(session, subject_id=subject.id),
    )


@router.delete(
    "/{subject_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def remove_subject(
    subject_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> Response:
    delete_subject(session, user_id=user.id, subject_id=subject_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
