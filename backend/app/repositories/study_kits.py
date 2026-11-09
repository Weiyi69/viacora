from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import StudyKit, StudySpace, Subject


def list_study_kits(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    include_archived: bool,
    page: int,
    page_size: int,
) -> tuple[list[StudyKit], int]:
    filters = [
        StudyKit.user_id == user_id,
        StudyKit.study_space_id == study_space_id,
        Subject.user_id == user_id,
        Subject.archived_at.is_(None),
        StudySpace.archived_at.is_(None),
    ]
    if not include_archived:
        filters.append(StudyKit.archived_at.is_(None))

    base = (
        select(StudyKit)
        .join(Subject, Subject.id == StudyKit.subject_id)
        .join(
            StudySpace,
            (StudySpace.id == StudyKit.study_space_id)
            & (StudySpace.subject_id == StudyKit.subject_id),
        )
        .where(*filters)
    )
    total = int(session.scalar(select(func.count()).select_from(base.subquery())) or 0)
    kits = list(
        session.scalars(
            base.order_by(StudyKit.updated_at.desc(), StudyKit.id)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return kits, total


def get_study_kit(
    session: Session,
    *,
    user_id: UUID,
    study_kit_id: UUID,
    lock: bool = False,
) -> StudyKit | None:
    statement = (
        select(StudyKit)
        .join(Subject, Subject.id == StudyKit.subject_id)
        .join(
            StudySpace,
            (StudySpace.id == StudyKit.study_space_id)
            & (StudySpace.subject_id == StudyKit.subject_id),
        )
        .where(
            StudyKit.id == study_kit_id,
            StudyKit.user_id == user_id,
            Subject.user_id == user_id,
            Subject.archived_at.is_(None),
            StudySpace.archived_at.is_(None),
        )
    )
    if lock:
        statement = statement.with_for_update(of=StudyKit)
    return session.scalar(statement)
