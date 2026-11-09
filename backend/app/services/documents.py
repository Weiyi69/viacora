import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

from fastapi import HTTPException, UploadFile, status
from sqlalchemy import delete, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    Document,
    DocumentPage,
    DocumentUploadAttempt,
    User,
)
from app.repositories import documents as document_repository
from app.services.document_storage import (
    InvalidPdfUpload,
    UploadTooLarge,
    remove_storage_file,
    safe_original_filename,
    store_upload,
    storage_path,
)
from app.services.study_material_jobs import delete_document_dependent_work
from app.services.study_spaces import get_study_space
from app.services.subjects import get_subject
from app.services.topics import get_topic

logger = logging.getLogger(__name__)
MAX_PROCESSING_ATTEMPTS = 3
UPLOAD_RATE_WINDOW = timedelta(hours=1)
UPLOAD_ATTEMPT_RETENTION = timedelta(days=1)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")


def check_upload_rate_limit(session: Session, *, user_id: UUID) -> None:
    now = datetime.now(timezone.utc)
    window_start = now - UPLOAD_RATE_WINDOW
    try:
        session.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one()
        session.execute(
            delete(DocumentUploadAttempt).where(
                DocumentUploadAttempt.user_id == user_id,
                DocumentUploadAttempt.attempted_at < now - UPLOAD_ATTEMPT_RETENTION,
            )
        )
        attempts = list(
            session.scalars(
                select(DocumentUploadAttempt.attempted_at)
                .where(
                    DocumentUploadAttempt.user_id == user_id,
                    DocumentUploadAttempt.attempted_at >= window_start,
                )
                .order_by(DocumentUploadAttempt.attempted_at)
            )
        )
        limit = get_settings().document_uploads_per_hour
        if len(attempts) >= limit:
            oldest_attempt = attempts[0]
            if oldest_attempt.tzinfo is None:
                oldest_attempt = oldest_attempt.replace(tzinfo=timezone.utc)
            retry_after = max(
                1,
                math.ceil(
                    (oldest_attempt + UPLOAD_RATE_WINDOW - now).total_seconds()
                ),
            )
            session.rollback()
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Upload rate limit exceeded",
                headers={"Retry-After": str(retry_after)},
            )
        session.add(DocumentUploadAttempt(user_id=user_id))
        session.commit()
    except HTTPException:
        raise
    except SQLAlchemyError:
        session.rollback()
        raise


def _validate_hierarchy(
    session: Session,
    *,
    user_id: UUID,
    subject_id: UUID,
    study_space_id: UUID,
    topic_id: UUID | None,
) -> None:
    get_subject(session, user_id=user_id, subject_id=subject_id)
    study_space = get_study_space(
        session, user_id=user_id, study_space_id=study_space_id
    )
    if study_space.subject_id != subject_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Study space does not belong to the requested subject",
        )
    if topic_id is not None:
        topic = get_topic(session, user_id=user_id, topic_id=topic_id)
        if topic.study_space_id != study_space_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Topic does not belong to the requested study space",
            )


async def upload_document(
    session: Session,
    *,
    user_id: UUID,
    subject_id: UUID,
    study_space_id: UUID,
    topic_id: UUID | None,
    upload: UploadFile,
) -> Document:
    _validate_hierarchy(
        session,
        user_id=user_id,
        subject_id=subject_id,
        study_space_id=study_space_id,
        topic_id=topic_id,
    )
    settings = get_settings()

    try:
        filename = safe_original_filename(upload.filename)
        session.execute(select(User.id).where(User.id == user_id).with_for_update()).scalar_one()
        used_bytes = document_repository.storage_usage(session, user_id=user_id)
        if used_bytes >= settings.document_storage_quota_bytes:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Document storage quota exceeded",
            )

        storage_key, file_size, content_sha256 = await store_upload(
            upload, max_bytes=settings.document_max_upload_bytes
        )
        if used_bytes + file_size > settings.document_storage_quota_bytes:
            try:
                remove_storage_file(storage_key)
            except OSError as error:
                logger.exception("Could not remove over-quota private document file")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Upload cleanup failed",
                ) from error
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Document storage quota exceeded",
            )
        get_subject(session, user_id=user_id, subject_id=subject_id, lock=True)
        study_space = get_study_space(
            session,
            user_id=user_id,
            study_space_id=study_space_id,
            lock=True,
        )
        if study_space.subject_id != subject_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Study space does not belong to the requested subject",
            )
        _validate_hierarchy(
            session,
            user_id=user_id,
            subject_id=subject_id,
            study_space_id=study_space_id,
            topic_id=topic_id,
        )
        document = Document(
            user_id=user_id,
            subject_id=subject_id,
            study_space_id=study_space_id,
            topic_id=topic_id,
            original_filename=filename,
            storage_key=storage_key,
            content_sha256=content_sha256,
            file_size_bytes=file_size,
        )
        session.add(document)
        session.commit()
        return document
    except UploadTooLarge as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="PDF exceeds the maximum file size",
        ) from error
    except InvalidPdfUpload as error:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Upload must be a PDF file",
        ) from error
    except HTTPException:
        session.rollback()
        if "storage_key" in locals():
            try:
                remove_storage_file(storage_key)
            except OSError as error:
                logger.exception("Could not remove unreferenced document file")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Upload cleanup failed",
                ) from error
        raise
    except SQLAlchemyError:
        session.rollback()
        if "storage_key" in locals():
            try:
                remove_storage_file(storage_key)
            except OSError:
                logger.exception("Could not remove unreferenced document file")
        raise


def get_document(session: Session, *, user_id: UUID, document_id: UUID) -> Document:
    document = document_repository.get_active_document(
        session, user_id=user_id, document_id=document_id
    )
    if document is None:
        raise _not_found()
    return document


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
) -> tuple[list[Document], int, int]:
    documents, total = document_repository.list_documents(
        session,
        user_id=user_id,
        subject_id=subject_id,
        study_space_id=study_space_id,
        topic_id=topic_id,
        status_filter=status_filter,
        query=query,
        page=page,
        page_size=page_size,
    )
    return documents, total, document_repository.storage_usage(session, user_id=user_id)


def list_document_pages(
    session: Session,
    *,
    user_id: UUID,
    document_id: UUID,
    page: int,
    page_size: int,
) -> tuple[list[DocumentPage], int]:
    document = get_document(session, user_id=user_id, document_id=document_id)
    if document.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Extracted text is not available",
        )
    if not document.has_extractable_text:
        return [], 0
    return document_repository.list_document_pages(
        session, document_id=document_id, page=page, page_size=page_size
    )


def assign_document_topic(
    session: Session,
    *,
    user_id: UUID,
    document_id: UUID,
    topic_id: UUID | None,
) -> Document:
    document = document_repository.get_active_document(
        session, user_id=user_id, document_id=document_id, lock=True
    )
    if document is None:
        raise _not_found()
    if topic_id is not None:
        topic = get_topic(session, user_id=user_id, topic_id=topic_id)
        if topic.study_space_id != document.study_space_id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Topic does not belong to the document's study space",
            )
    document.topic_id = topic_id
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    return document


def retry_document(session: Session, *, user_id: UUID, document_id: UUID) -> Document:
    document = document_repository.get_active_document(
        session, user_id=user_id, document_id=document_id, lock=True
    )
    if document is None:
        raise _not_found()
    if (
        document.status != "queued"
        or not document.retryable
        or document.attempts_used >= MAX_PROCESSING_ATTEMPTS
        or document.next_attempt_at is None
    ):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Document is not eligible for retry",
        )
    document.next_attempt_at = datetime.now(timezone.utc)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    return document


def get_document_file(
    session: Session,
    *,
    user_id: UUID,
    document_id: UUID,
) -> tuple[Path, str]:
    document = get_document(session, user_id=user_id, document_id=document_id)
    path = storage_path(document.storage_key)
    if not path.is_file():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Document file is temporarily unavailable",
        )
    return path, document.original_filename


def delete_document(session: Session, *, user_id: UUID, document_id: UUID) -> None:
    document = document_repository.get_active_document(
        session, user_id=user_id, document_id=document_id, lock=True
    )
    if document is None:
        raise _not_found()
    storage_key = document.storage_key
    delete_document_dependent_work(session, [document.id])
    session.delete(document)
    try:
        session.commit()
    except SQLAlchemyError:
        session.rollback()
        raise
    try:
        remove_storage_file(storage_key)
    except OSError as error:
        logger.exception("Deleted document metadata but private file cleanup failed")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Document was deleted but private file cleanup failed",
        ) from error
