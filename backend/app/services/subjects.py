from datetime import datetime, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import LearningActivity, Subject
from app.repositories import subjects as subject_repository
from app.schemas.learning import SubjectCreate, SubjectUpdate


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Subject not found")


def list_subjects(
    session: Session,
    *,
    user_id: UUID,
    query: str | None,
    include_archived: bool,
    page: int,
    page_size: int,
) -> tuple[list[tuple[Subject, int]], int]:
    return subject_repository.list_subjects(
        session,
        user_id=user_id,
        query=query,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )


def get_subject(
    session: Session,
    *,
    user_id: UUID,
    subject_id: UUID,
    allow_archived: bool = False,
    lock: bool = False,
) -> Subject:
    subject = subject_repository.get_subject(
        session, user_id=user_id, subject_id=subject_id, lock=lock
    )
    if subject is None or (subject.archived_at is not None and not allow_archived):
        raise _not_found()
    return subject


def create_subject(
    session: Session,
    *,
    user_id: UUID,
    payload: SubjectCreate,
) -> Subject:
    subject = subject_repository.create_subject(
        session,
        user_id=user_id,
        name=payload.name,
        description=payload.description,
        color=payload.color,
        icon=payload.icon,
    )
    session.add(
        LearningActivity(
            user_id=user_id,
            event_type="subject_created",
            subject_id=subject.id,
            resource_name=subject.name,
        )
    )
    try:
        session.commit()
        session.refresh(subject)
    except SQLAlchemyError:
        session.rollback()
        raise
    return subject


def update_subject(
    session: Session,
    *,
    user_id: UUID,
    subject_id: UUID,
    payload: SubjectUpdate,
) -> Subject:
    subject = get_subject(session, user_id=user_id, subject_id=subject_id)
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(subject, field, value)
    try:
        session.commit()
        session.refresh(subject)
    except SQLAlchemyError:
        session.rollback()
        raise
    return subject


def archive_subject(session: Session, *, user_id: UUID, subject_id: UUID) -> Subject:
    subject = get_subject(
        session, user_id=user_id, subject_id=subject_id, allow_archived=True
    )
    if subject.archived_at is None:
        subject.archived_at = datetime.now(timezone.utc)
        try:
            session.commit()
            session.refresh(subject)
        except SQLAlchemyError:
            session.rollback()
            raise
    return subject


def restore_subject(session: Session, *, user_id: UUID, subject_id: UUID) -> Subject:
    subject = get_subject(
        session, user_id=user_id, subject_id=subject_id, allow_archived=True
    )
    if subject.archived_at is not None:
        subject.archived_at = None
        try:
            session.commit()
            session.refresh(subject)
        except SQLAlchemyError:
            session.rollback()
            raise
    return subject


def delete_subject(session: Session, *, user_id: UUID, subject_id: UUID) -> None:
    subject = get_subject(
        session, user_id=user_id, subject_id=subject_id, allow_archived=True
    )
    session.delete(subject)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
