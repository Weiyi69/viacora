from datetime import datetime, timezone
from uuid import UUID, uuid4

from fastapi import HTTPException, status
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.models import StudyKit
from app.repositories import study_kits as study_kit_repository
from app.schemas.study_kits import StudyKitCreate, StudyKitUpdate
from app.services.study_material_jobs import cancel_jobs_for_kit, delete_study_kit_jobs
from app.services.study_spaces import get_study_space


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Study kit not found")


def list_study_kits(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    include_archived: bool,
    page: int,
    page_size: int,
) -> tuple[list[StudyKit], int]:
    get_study_space(session, user_id=user_id, study_space_id=study_space_id)
    return study_kit_repository.list_study_kits(
        session,
        user_id=user_id,
        study_space_id=study_space_id,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )


def get_study_kit(
    session: Session,
    *,
    user_id: UUID,
    study_kit_id: UUID,
    allow_archived: bool = False,
    lock: bool = False,
) -> StudyKit:
    kit = study_kit_repository.get_study_kit(
        session, user_id=user_id, study_kit_id=study_kit_id, lock=lock
    )
    if kit is None or (kit.archived_at is not None and not allow_archived):
        raise _not_found()
    return kit


def create_study_kit(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    payload: StudyKitCreate,
) -> StudyKit:
    study_space = get_study_space(
        session, user_id=user_id, study_space_id=study_space_id, lock=True
    )
    kit = StudyKit(
        id=uuid4(),
        user_id=user_id,
        subject_id=study_space.subject_id,
        study_space_id=study_space.id,
        title=payload.title,
        description=payload.description,
    )
    session.add(kit)
    try:
        session.commit()
        session.refresh(kit)
    except SQLAlchemyError:
        session.rollback()
        raise
    return kit


def update_study_kit(
    session: Session,
    *,
    user_id: UUID,
    study_kit_id: UUID,
    payload: StudyKitUpdate,
) -> StudyKit:
    kit = get_study_kit(
        session, user_id=user_id, study_kit_id=study_kit_id, lock=True
    )
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(kit, field, value)
    try:
        session.commit()
        session.refresh(kit)
    except SQLAlchemyError:
        session.rollback()
        raise
    return kit


def archive_study_kit(
    session: Session, *, user_id: UUID, study_kit_id: UUID
) -> StudyKit:
    kit = get_study_kit(
        session,
        user_id=user_id,
        study_kit_id=study_kit_id,
        allow_archived=True,
        lock=True,
    )
    if kit.archived_at is None:
        kit.archived_at = datetime.now(timezone.utc)
        cancel_jobs_for_kit(session, kit.id)
        try:
            session.commit()
            session.refresh(kit)
        except SQLAlchemyError:
            session.rollback()
            raise
    return kit


def restore_study_kit(
    session: Session, *, user_id: UUID, study_kit_id: UUID
) -> StudyKit:
    kit = get_study_kit(
        session,
        user_id=user_id,
        study_kit_id=study_kit_id,
        allow_archived=True,
        lock=True,
    )
    if kit.archived_at is not None:
        kit.archived_at = None
        try:
            session.commit()
            session.refresh(kit)
        except SQLAlchemyError:
            session.rollback()
            raise
    return kit


def delete_study_kit(
    session: Session, *, user_id: UUID, study_kit_id: UUID
) -> None:
    snapshot = study_kit_repository.get_study_kit(
        session, user_id=user_id, study_kit_id=study_kit_id
    )
    if snapshot is None:
        raise _not_found()
    get_study_space(
        session,
        user_id=user_id,
        study_space_id=snapshot.study_space_id,
        allow_archived=True,
        lock=True,
    )
    kit = get_study_kit(
        session,
        user_id=user_id,
        study_kit_id=study_kit_id,
        allow_archived=True,
        lock=True,
    )
    delete_study_kit_jobs(session, kit.id)
    session.delete(kit)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
