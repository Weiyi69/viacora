from uuid import UUID

from sqlalchemy import case, func, or_, select, update
from sqlalchemy.orm import Session

from app.models import Document, DocumentPage, StudySpace, Subject


def get_active_document(
    session: Session, *, user_id: UUID, document_id: UUID, lock: bool = False
) -> Document | None:
    statement = (
        select(Document)
        .join(Subject, Subject.id == Document.subject_id)
        .join(StudySpace, StudySpace.id == Document.study_space_id)
        .where(
            Document.id == document_id,
            Document.user_id == user_id,
            Subject.user_id == user_id,
            Subject.archived_at.is_(None),
            StudySpace.archived_at.is_(None),
        )
    )
    if lock:
        statement = statement.with_for_update(of=Document)
    return session.scalar(statement)


def get_document(
    session: Session, *, user_id: UUID, document_id: UUID, lock: bool = False
) -> Document | None:
    statement = select(Document).where(
        Document.id == document_id,
        Document.user_id == user_id,
    )
    if lock:
        statement = statement.with_for_update(of=Document)
    return session.scalar(statement)


def list_documents(
    session: Session,
    *,
    user_id: UUID,
    subject_id: UUID | None,
    study_space_id: UUID | None,
    topic_id: UUID | None,
    status_filter: str | None,
    query: str | None,
    page: int,
    page_size: int,
) -> tuple[list[Document], int]:
    statement = (
        select(Document)
        .join(Subject, Subject.id == Document.subject_id)
        .join(StudySpace, StudySpace.id == Document.study_space_id)
        .where(
            Document.user_id == user_id,
            Subject.archived_at.is_(None),
            StudySpace.archived_at.is_(None),
        )
    )
    if subject_id is not None:
        statement = statement.where(Document.subject_id == subject_id)
    if study_space_id is not None:
        statement = statement.where(Document.study_space_id == study_space_id)
    if topic_id is not None:
        statement = statement.where(Document.topic_id == topic_id)
    if status_filter is not None:
        statement = statement.where(Document.status == status_filter)
    if query:
        pattern = f"%{query}%"
        statement = statement.where(
            or_(Document.original_filename.ilike(pattern), Document.error_code.ilike(pattern))
        )
    total = session.scalar(select(func.count()).select_from(statement.subquery())) or 0
    documents = list(
        session.scalars(
            statement.order_by(Document.created_at.desc(), Document.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return documents, total


def list_document_pages(
    session: Session, *, document_id: UUID, page: int, page_size: int
) -> tuple[list[DocumentPage], int]:
    statement = select(DocumentPage).where(DocumentPage.document_id == document_id)
    total = session.scalar(
        select(func.count()).select_from(statement.subquery())
    ) or 0
    pages = list(
        session.scalars(
            statement.order_by(DocumentPage.page_number)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    )
    return pages, total


def storage_usage(session: Session, *, user_id: UUID) -> int:
    return (
        session.scalar(
            select(func.coalesce(func.sum(Document.file_size_bytes), 0)).where(
                Document.user_id == user_id,
            )
        )
        or 0
    )


def revoke_processing_for_parent(
    session: Session,
    *,
    subject_id: UUID | None = None,
    study_space_id: UUID | None = None,
) -> None:
    if subject_id is None and study_space_id is None:
        raise ValueError("A document parent is required")
    statement = update(Document).where(Document.status == "processing")
    if subject_id is not None:
        statement = statement.where(Document.subject_id == subject_id)
    if study_space_id is not None:
        statement = statement.where(Document.study_space_id == study_space_id)
    session.execute(
        statement.values(
            status="queued",
            attempts_used=case(
                (Document.attempts_used > 0, Document.attempts_used - 1),
                else_=0,
            ),
            retryable=False,
            error_code=None,
            next_attempt_at=None,
            lease_token=None,
            lease_expires_at=None,
        )
    )


def detach_documents_from_topic(
    session: Session, *, topic_id: UUID
) -> None:
    session.execute(
        update(Document)
        .where(Document.topic_id == topic_id)
        .values(topic_id=None)
    )


def storage_keys_for_parent(
    session: Session,
    *,
    subject_id: UUID | None = None,
    study_space_id: UUID | None = None,
) -> list[str]:
    statement = select(Document.storage_key)
    if subject_id is not None:
        statement = statement.where(Document.subject_id == subject_id)
    if study_space_id is not None:
        statement = statement.where(Document.study_space_id == study_space_id)
    return list(session.scalars(statement))
