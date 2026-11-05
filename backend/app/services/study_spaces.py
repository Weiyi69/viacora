from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import LearningActivity, StudySpace
from app.repositories import study_spaces as study_space_repository
from app.schemas.learning import StudySpaceCreate, StudySpaceUpdate
from app.services.subjects import get_subject


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study space not found")


def _require_active_parent(session: Session, *, user_id: UUID, subject_id: UUID) -> None:
    get_subject(session, user_id=user_id, subject_id=subject_id)


def list_study_spaces(
    session: Session,
    *,
    user_id: UUID,
    subject_id: UUID,
    query: str | None,
    sort_by: str,
    include_archived: bool,
    page: int,
    page_size: int,
) -> tuple[list[tuple[StudySpace, int]], int]:
    _require_active_parent(session, user_id=user_id, subject_id=subject_id)
    return study_space_repository.list_study_spaces(
        session,
        user_id=user_id,
        subject_id=subject_id,
        query=query,
        sort_by=sort_by,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )


def get_study_space(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    allow_archived: bool = False,
    lock: bool = False,
) -> StudySpace:
    study_space = study_space_repository.get_study_space(
        session, user_id=user_id, study_space_id=study_space_id, lock=lock
    )
    if study_space is None:
        raise _not_found()
    _require_active_parent(
        session, user_id=user_id, subject_id=study_space.subject_id
    )
    if study_space.archived_at is not None and not allow_archived:
        raise _not_found()
    return study_space


def create_study_space(
    session: Session,
    *,
    user_id: UUID,
    subject_id: UUID,
    payload: StudySpaceCreate,
) -> StudySpace:
    _require_active_parent(session, user_id=user_id, subject_id=subject_id)
    study_space = study_space_repository.create_study_space(
        session,
        subject_id=subject_id,
        name=payload.name,
        description=payload.description,
    )
    session.add(
        LearningActivity(
            user_id=user_id,
            event_type="study_space_created",
            study_space_id=study_space.id,
            resource_name=study_space.name,
        )
    )
    try:
        session.commit()
        session.refresh(study_space)
    except SQLAlchemyError:
        session.rollback()
        raise
    return study_space


def update_study_space(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    payload: StudySpaceUpdate,
) -> StudySpace:
    study_space = get_study_space(
        session, user_id=user_id, study_space_id=study_space_id
    )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(study_space, field, value)
    try:
        session.commit()
        session.refresh(study_space)
    except SQLAlchemyError:
        session.rollback()
        raise
    return study_space


def archive_study_space(
    session: Session, *, user_id: UUID, study_space_id: UUID
) -> StudySpace:
    study_space = get_study_space(
        session,
        user_id=user_id,
        study_space_id=study_space_id,
        allow_archived=True,
    )
    if study_space.archived_at is None:
        study_space.archived_at = datetime.now(timezone.utc)
        try:
            session.commit()
            session.refresh(study_space)
        except SQLAlchemyError:
            session.rollback()
            raise
    return study_space


def restore_study_space(
    session: Session, *, user_id: UUID, study_space_id: UUID
) -> StudySpace:
    study_space = get_study_space(
        session,
        user_id=user_id,
        study_space_id=study_space_id,
        allow_archived=True,
    )
    if study_space.archived_at is not None:
        study_space.archived_at = None
        try:
            session.commit()
            session.refresh(study_space)
        except SQLAlchemyError:
            session.rollback()
            raise
    return study_space


def delete_study_space(
    session: Session, *, user_id: UUID, study_space_id: UUID
) -> None:
    study_space = get_study_space(
        session,
        user_id=user_id,
        study_space_id=study_space_id,
        allow_archived=True,
    )
    session.delete(study_space)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
