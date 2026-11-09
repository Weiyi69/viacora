from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import StudySpace, Subject, Topic


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
    filters = [Subject.user_id == user_id, Subject.id == subject_id, Subject.archived_at.is_(None)]
    if not include_archived:
        filters.append(StudySpace.archived_at.is_(None))
    if query:
        filters.append(StudySpace.name.ilike(f"%{query}%"))

    count_query = (
        select(func.count())
        .select_from(StudySpace)
        .join(Subject, Subject.id == StudySpace.subject_id)
        .where(*filters)
    )
    total = int(session.scalar(count_query) or 0)
    active_topic_count = (
        select(func.count(Topic.id))
        .where(
            Topic.study_space_id == StudySpace.id,
        )
        .correlate(StudySpace)
        .scalar_subquery()
    )
    statement = (
        select(StudySpace, active_topic_count.label("topic_count"))
        .join(Subject, Subject.id == StudySpace.subject_id)
        .where(*filters)
    )
    if sort_by == "name":
        statement = statement.order_by(func.lower(StudySpace.name), StudySpace.id)
    else:
        statement = statement.order_by(StudySpace.updated_at.desc(), StudySpace.id)
    rows = session.execute(
        statement.offset((page - 1) * page_size).limit(page_size)
    ).all()
    return [(study_space, count) for study_space, count in rows], total


def get_study_space(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    lock: bool = False,
) -> StudySpace | None:
    statement = (
        select(StudySpace)
        .join(Subject, Subject.id == StudySpace.subject_id)
        .where(StudySpace.id == study_space_id, Subject.user_id == user_id)
    )
    if lock:
        statement = statement.with_for_update(of=StudySpace)
    return session.scalar(statement)


def count_topics(session: Session, *, study_space_id: UUID) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(Topic).where(
                Topic.study_space_id == study_space_id
            )
        )
        or 0
    )


def create_study_space(
    session: Session,
    *,
    subject_id: UUID,
    name: str,
    description: str | None,
) -> StudySpace:
    study_space = StudySpace(
        subject_id=subject_id,
        name=name,
        description=description,
    )
    session.add(study_space)
    session.flush()
    return study_space
