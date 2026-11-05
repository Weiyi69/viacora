from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import StudySpace, Subject


def list_subjects(
    session: Session,
    *,
    user_id: UUID,
    query: str | None,
    include_archived: bool,
    page: int,
    page_size: int,
) -> tuple[list[tuple[Subject, int]], int]:
    filters = [Subject.user_id == user_id]
    if not include_archived:
        filters.append(Subject.archived_at.is_(None))
    if query:
        filters.append(Subject.name.ilike(f"%{query}%"))

    total = int(
        session.scalar(select(func.count()).select_from(Subject).where(*filters)) or 0
    )
    active_space_count = (
        select(func.count(StudySpace.id))
        .where(
            StudySpace.subject_id == Subject.id,
            StudySpace.archived_at.is_(None),
            Subject.archived_at.is_(None),
        )
        .correlate(Subject)
        .scalar_subquery()
    )
    rows = session.execute(
        select(Subject, active_space_count.label("study_space_count"))
        .where(*filters)
        .order_by(Subject.updated_at.desc(), Subject.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    ).all()
    return [(subject, count) for subject, count in rows], total


def get_subject(
    session: Session,
    *,
    user_id: UUID,
    subject_id: UUID,
    lock: bool = False,
) -> Subject | None:
    statement = select(Subject).where(
        Subject.id == subject_id, Subject.user_id == user_id
    )
    if lock:
        statement = statement.with_for_update()
    return session.scalar(statement)


def count_active_study_spaces(
    session: Session, *, subject_id: UUID
) -> int:
    return int(
        session.scalar(
            select(func.count()).select_from(StudySpace).where(
                StudySpace.subject_id == subject_id,
                StudySpace.archived_at.is_(None),
            )
        )
        or 0
    )


def create_subject(
    session: Session,
    *,
    user_id: UUID,
    name: str,
    description: str | None,
    color: str | None,
    icon: str | None,
) -> Subject:
    subject = Subject(
        user_id=user_id,
        name=name,
        description=description,
        color=color,
        icon=icon,
    )
    session.add(subject)
    session.flush()
    return subject
