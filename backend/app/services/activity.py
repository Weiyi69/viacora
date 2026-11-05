from datetime import datetime, timedelta, timezone
from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import LearningActivity, User
from app.repositories import learning_activity as activity_repository
from app.repositories import study_spaces as study_space_repository
from app.repositories import subjects as subject_repository
from app.schemas.learning import ActivityAccessRequest
from app.services.study_spaces import get_study_space
from app.services.subjects import get_subject

ACCESS_DEDUPLICATION_INTERVAL = timedelta(minutes=5)


def list_activity(
    session: Session,
    *,
    user_id: UUID,
    page: int,
    page_size: int,
) -> tuple[list[LearningActivity], int]:
    return activity_repository.list_activity(
        session, user_id=user_id, page=page, page_size=page_size
    )


def list_recent_access(
    session: Session, *, user_id: UUID, page_size: int
) -> list[LearningActivity]:
    return activity_repository.list_recent_access(
        session, user_id=user_id, page_size=page_size
    )


def record_access(
    session: Session,
    *,
    user_id: UUID,
    payload: ActivityAccessRequest,
) -> LearningActivity:
    if payload.resource_type == "subject":
        resource = get_subject(
            session, user_id=user_id, subject_id=payload.resource_id
        )
        event_type = "subject_accessed"
        resource_kwargs = {"subject_id": resource.id}
    else:
        resource = get_study_space(
            session, user_id=user_id, study_space_id=payload.resource_id
        )
        event_type = "study_space_accessed"
        resource_kwargs = {"study_space_id": resource.id}

    try:
        # Serialize access-event deduplication per account on PostgreSQL.
        owner_id = session.scalar(
            select(User.id).where(User.id == user_id).with_for_update()
        )
        if owner_id is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
            )
        if payload.resource_type == "subject":
            resource = subject_repository.get_subject(
                session,
                user_id=user_id,
                subject_id=payload.resource_id,
                lock=True,
            )
            if resource is None or resource.archived_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
                )
        else:
            resource = study_space_repository.get_study_space(
                session,
                user_id=user_id,
                study_space_id=payload.resource_id,
                lock=True,
            )
            if resource is None or resource.archived_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
                )
            parent = subject_repository.get_subject(
                session, user_id=user_id, subject_id=resource.subject_id, lock=True
            )
            if parent is None or parent.archived_at is not None:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND, detail="Resource not found"
                )
        since = datetime.now(timezone.utc) - ACCESS_DEDUPLICATION_INTERVAL
        existing = activity_repository.find_recent_access(
            session,
            user_id=user_id,
            event_type=event_type,
            resource_id=payload.resource_id,
            since=since,
        )
        if existing is not None:
            session.commit()
            return existing
        activity = LearningActivity(
            user_id=user_id,
            event_type=event_type,
            resource_name=resource.name,
            **resource_kwargs,
        )
        session.add(activity)
        session.commit()
        session.refresh(activity)
        return activity
    except SQLAlchemyError:
        session.rollback()
        raise
