from datetime import datetime
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models import LearningActivity, StudySpace, Subject


def list_activity(
    session: Session,
    *,
    user_id: UUID,
    page: int,
    page_size: int,
    access_only: bool = False,
) -> tuple[list[LearningActivity], int]:
    filters = [LearningActivity.user_id == user_id]
    if access_only:
        filters.append(
            LearningActivity.event_type.in_(("subject_accessed", "study_space_accessed"))
        )
    total = int(
        session.scalar(
            select(func.count()).select_from(LearningActivity).where(*filters)
        )
        or 0
    )
    items = list(
        session.scalars(
            select(LearningActivity)
            .where(*filters)
            .order_by(LearningActivity.occurred_at.desc(), LearningActivity.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total


def find_recent_access(
    session: Session,
    *,
    user_id: UUID,
    event_type: str,
    resource_id: UUID,
    since: datetime,
) -> LearningActivity | None:
    resource_filter = (
        LearningActivity.subject_id == resource_id
        if event_type == "subject_accessed"
        else LearningActivity.study_space_id == resource_id
    )
    return session.scalar(
        select(LearningActivity)
        .where(
            LearningActivity.user_id == user_id,
            LearningActivity.event_type == event_type,
            resource_filter,
            LearningActivity.occurred_at >= since,
        )
        .order_by(LearningActivity.occurred_at.desc())
        .limit(1)
    )


def list_recent_access(
    session: Session, *, user_id: UUID, page_size: int
) -> list[LearningActivity]:
    active_subject_access = (
        select(Subject.id)
        .where(
            Subject.id == LearningActivity.subject_id,
            Subject.user_id == user_id,
            Subject.archived_at.is_(None),
        )
        .exists()
    )
    active_study_space_access = (
        select(StudySpace.id)
        .join(Subject, Subject.id == StudySpace.subject_id)
        .where(
            StudySpace.id == LearningActivity.study_space_id,
            Subject.user_id == user_id,
            Subject.archived_at.is_(None),
            StudySpace.archived_at.is_(None),
        )
        .exists()
    )
    return list(
        session.scalars(
            select(LearningActivity)
            .where(
                LearningActivity.user_id == user_id,
                or_(
                    (
                        (LearningActivity.event_type == "subject_accessed")
                        & active_subject_access
                    ),
                    (
                        (LearningActivity.event_type == "study_space_accessed")
                        & active_study_space_access
                    ),
                ),
            )
            .order_by(LearningActivity.occurred_at.desc(), LearningActivity.id)
            .limit(page_size)
        )
    )


def count_active_subjects(session: Session, *, user_id: UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(Subject)
            .where(Subject.user_id == user_id, Subject.archived_at.is_(None))
        )
        or 0
    )


def count_active_study_spaces(session: Session, *, user_id: UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(StudySpace)
            .join(Subject, Subject.id == StudySpace.subject_id)
            .where(
                Subject.user_id == user_id,
                Subject.archived_at.is_(None),
                StudySpace.archived_at.is_(None),
            )
        )
        or 0
    )
