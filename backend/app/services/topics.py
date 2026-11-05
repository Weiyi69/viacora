from uuid import UUID

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import LearningActivity, Topic
from app.repositories import documents as document_repository
from app.repositories import topics as topic_repository
from app.schemas.learning import TopicCreate, TopicUpdate
from app.services.study_spaces import get_study_space


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Topic not found")


def _require_active_space(
    session: Session, *, user_id: UUID, study_space_id: UUID, lock: bool = False
) -> None:
    get_study_space(
        session, user_id=user_id, study_space_id=study_space_id, lock=lock
    )


def list_topics(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    query: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Topic], int]:
    _require_active_space(
        session, user_id=user_id, study_space_id=study_space_id
    )
    return topic_repository.list_topics(
        session,
        user_id=user_id,
        study_space_id=study_space_id,
        query=query,
        page=page,
        page_size=page_size,
    )


def get_topic(session: Session, *, user_id: UUID, topic_id: UUID) -> Topic:
    topic = topic_repository.get_topic(session, user_id=user_id, topic_id=topic_id)
    if topic is None:
        raise _not_found()
    _require_active_space(
        session, user_id=user_id, study_space_id=topic.study_space_id
    )
    return topic


def create_topic(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    payload: TopicCreate,
) -> Topic:
    _require_active_space(
        session, user_id=user_id, study_space_id=study_space_id, lock=True
    )
    position = payload.position
    if position is None:
        highest_position = session.scalar(
            select(Topic.position)
            .where(Topic.study_space_id == study_space_id)
            .order_by(Topic.position.desc())
            .limit(1)
        )
        position = 0 if highest_position is None else highest_position + 1
    existing_topics = topic_repository.list_topics_for_ordering(
        session, study_space_id=study_space_id
    )
    insertion_index = min(position, len(existing_topics))
    position = len(existing_topics)
    topic = topic_repository.create_topic(
        session,
        study_space_id=study_space_id,
        name=payload.name,
        description=payload.description,
        position=position,
    )
    ordered_topics = [*existing_topics]
    ordered_topics.insert(insertion_index, topic)
    for next_position, existing_topic in enumerate(ordered_topics):
        existing_topic.position = next_position
    session.add(
        LearningActivity(
            user_id=user_id,
            event_type="topic_created",
            topic_id=topic.id,
            resource_name=topic.name,
        )
    )
    try:
        session.commit()
        session.refresh(topic)
    except SQLAlchemyError:
        session.rollback()
        raise
    return topic


def update_topic(
    session: Session,
    *,
    user_id: UUID,
    topic_id: UUID,
    payload: TopicUpdate,
) -> Topic:
    topic = get_topic(session, user_id=user_id, topic_id=topic_id)
    updates = payload.model_dump(exclude_unset=True)
    if "position" in updates:
        _require_active_space(
            session,
            user_id=user_id,
            study_space_id=topic.study_space_id,
            lock=True,
        )
        ordered_topics = topic_repository.list_topics_for_ordering(
            session, study_space_id=topic.study_space_id
        )
        ordered_topics.remove(topic)
        insertion_index = min(updates.pop("position"), len(ordered_topics))
        ordered_topics.insert(insertion_index, topic)
        for next_position, ordered_topic in enumerate(ordered_topics):
            ordered_topic.position = next_position
    for field, value in updates.items():
        setattr(topic, field, value)
    try:
        session.commit()
        session.refresh(topic)
    except SQLAlchemyError:
        session.rollback()
        raise
    return topic


def delete_topic(session: Session, *, user_id: UUID, topic_id: UUID) -> None:
    topic = get_topic(session, user_id=user_id, topic_id=topic_id)
    document_repository.detach_documents_from_topic(session, topic_id=topic_id)
    session.delete(topic)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
