from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import StudySpace, Subject, Topic


def list_topics(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    query: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Topic], int]:
    filters = [
        Subject.user_id == user_id,
        Subject.archived_at.is_(None),
        StudySpace.archived_at.is_(None),
        StudySpace.id == study_space_id,
    ]
    if query:
        filters.append(Topic.name.ilike(f"%{query}%"))
    total = int(
        session.scalar(
            select(func.count())
            .select_from(Topic)
            .join(StudySpace, StudySpace.id == Topic.study_space_id)
            .join(Subject, Subject.id == StudySpace.subject_id)
            .where(*filters)
        )
        or 0
    )
    items = list(
        session.scalars(
            select(Topic)
            .join(StudySpace, StudySpace.id == Topic.study_space_id)
            .join(Subject, Subject.id == StudySpace.subject_id)
            .where(*filters)
            .order_by(Topic.position, Topic.created_at, Topic.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return items, total


def get_topic(
    session: Session,
    *,
    user_id: UUID,
    topic_id: UUID,
) -> Topic | None:
    return session.scalar(
        select(Topic)
        .join(StudySpace, StudySpace.id == Topic.study_space_id)
        .join(Subject, Subject.id == StudySpace.subject_id)
        .where(Topic.id == topic_id, Subject.user_id == user_id)
    )


def list_topics_for_ordering(
    session: Session, *, study_space_id: UUID
) -> list[Topic]:
    return list(
        session.scalars(
            select(Topic)
            .where(Topic.study_space_id == study_space_id)
            .order_by(Topic.position, Topic.created_at, Topic.id)
            .with_for_update()
        )
    )


def create_topic(
    session: Session,
    *,
    study_space_id: UUID,
    name: str,
    description: str | None,
    position: int,
) -> Topic:
    topic = Topic(
        study_space_id=study_space_id,
        name=name,
        description=description,
        position=position,
    )
    session.add(topic)
    session.flush()
    return topic
