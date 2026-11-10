import logging
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, delete, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import get_settings
from app.models import Document, DocumentPage, DocumentUploadAttempt, StudySpace, Subject
from app.services.document_storage import storage_path
from app.services.document_indexing import (
    ensure_index_generation,
    process_next_index_job,
    reconcile_missing_indexes,
)
from app.services.ai_usage_reservations import recover_expired_reservations
from app.services.study_material_jobs import recover_expired_study_material_jobs
from app.services.study_material_generation import process_next_study_material_job
from app.services.pdf_extraction import (
    PermanentPdfError,
    StaleDocumentLease,
    TransientPdfError,
    extract_pdf_pages,
)

logger = logging.getLogger(__name__)
MAX_PROCESSING_ATTEMPTS = 3
ORPHAN_GRACE_PERIOD = timedelta(hours=1)
ORPHAN_CLEANUP_INTERVAL = timedelta(hours=1)
AI_RECOVERY_INTERVAL = timedelta(minutes=1)
_STORAGE_FILENAME = re.compile(r"[a-f0-9]{32}\.pdf\Z")


@dataclass(frozen=True)
class DocumentLease:
    document_id: UUID
    user_id: UUID
    subject_id: UUID
    study_space_id: UUID
    storage_key: str
    token: UUID
    attempts_used: int


def _claim_document(session_factory: sessionmaker[Session]) -> DocumentLease | None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        eligible = or_(
            and_(
                Document.status == "queued",
                or_(Document.next_attempt_at.is_(None), Document.next_attempt_at <= now),
            ),
            and_(
                Document.status == "processing",
                or_(
                    Document.lease_expires_at.is_(None),
                    Document.lease_expires_at <= now,
                ),
            ),
        )
        document_id = session.scalar(
            select(Document.id)
            .join(Subject, Subject.id == Document.subject_id)
            .join(StudySpace, StudySpace.id == Document.study_space_id)
            .where(
                eligible,
                Subject.archived_at.is_(None),
                StudySpace.archived_at.is_(None),
            )
            .order_by(Document.created_at, Document.id)
            .limit(1)
            .with_for_update(skip_locked=True, of=Document)
        )
        if document_id is None:
            return None

        document = session.scalar(
            select(Document).where(Document.id == document_id).with_for_update()
        )
        if document is None:
            return None
        if document.attempts_used >= MAX_PROCESSING_ATTEMPTS:
            document.status = "failed"
            document.retryable = False
            document.error_code = "attempts_exhausted"
            document.next_attempt_at = None
            document.lease_token = None
            document.lease_expires_at = None
            document.processed_at = now
            session.commit()
            return None

        token = uuid4()
        document.status = "processing"
        document.attempts_used += 1
        document.retryable = False
        document.error_code = None
        document.next_attempt_at = None
        document.lease_token = token
        document.lease_expires_at = now + timedelta(
            seconds=settings.document_worker_lease_seconds
        )
        session.commit()
        return DocumentLease(
            document_id=document.id,
            user_id=document.user_id,
            subject_id=document.subject_id,
            study_space_id=document.study_space_id,
            storage_key=document.storage_key,
            token=token,
            attempts_used=document.attempts_used,
        )


def _lock_parents(session: Session, lease: DocumentLease) -> bool:
    subject = session.scalar(
        select(Subject)
        .where(
            Subject.id == lease.subject_id,
            Subject.user_id == lease.user_id,
            Subject.archived_at.is_(None),
        )
        .with_for_update()
    )
    study_space = session.scalar(
        select(StudySpace)
        .where(
            StudySpace.id == lease.study_space_id,
            StudySpace.subject_id == lease.subject_id,
            StudySpace.archived_at.is_(None),
        )
        .with_for_update()
    )
    return subject is not None and study_space is not None


def _revoke_lease(document: Document) -> None:
    document.status = "queued"
    document.attempts_used = max(0, document.attempts_used - 1)
    document.retryable = False
    document.error_code = None
    document.next_attempt_at = None
    document.lease_token = None
    document.lease_expires_at = None


def _lease_is_expired(lease_expires_at: datetime | None, now: datetime) -> bool:
    if lease_expires_at is None:
        return True
    if lease_expires_at.tzinfo is None:
        lease_expires_at = lease_expires_at.replace(tzinfo=timezone.utc)
    else:
        lease_expires_at = lease_expires_at.astimezone(timezone.utc)
    return lease_expires_at <= now


def _renew_lease(session_factory: sessionmaker[Session], lease: DocumentLease) -> bool:
    settings = get_settings()
    with session_factory() as session:
        parents_active = _lock_parents(session, lease)
        document = session.scalar(
            select(Document)
            .where(
                Document.id == lease.document_id,
                Document.status == "processing",
                Document.lease_token == lease.token,
            )
            .with_for_update()
        )
        if document is None:
            return False
        now = datetime.now(timezone.utc)
        if not parents_active:
            _revoke_lease(document)
            session.commit()
            return False
        if _lease_is_expired(document.lease_expires_at, now):
            return False
        document.lease_expires_at = now + timedelta(
            seconds=settings.document_worker_lease_seconds
        )
        session.commit()
        return True


def _publish_result(
    session_factory: sessionmaker[Session],
    lease: DocumentLease,
    pages: list[str],
    has_text: bool,
) -> None:
    with session_factory() as session:
        parents_active = _lock_parents(session, lease)
        document = session.scalar(
            select(Document)
            .where(
                Document.id == lease.document_id,
                Document.status == "processing",
                Document.lease_token == lease.token,
            )
            .with_for_update()
        )
        if document is None:
            return
        now = datetime.now(timezone.utc)
        if _lease_is_expired(document.lease_expires_at, now):
            return
        if not parents_active:
            _revoke_lease(document)
            session.commit()
            return

        document.pages.clear()
        document.pages.extend(
            DocumentPage(page_number=index, extracted_text=text)
            for index, text in enumerate(pages, start=1)
        )
        document.status = "completed"
        document.page_count = len(pages)
        document.has_extractable_text = has_text
        document.retryable = False
        document.error_code = None
        document.next_attempt_at = None
        settings = get_settings()
        if has_text and settings.ai_enabled:
            ensure_index_generation(session, document, settings=settings)
        now = datetime.now(timezone.utc)
        if _lease_is_expired(document.lease_expires_at, now):
            session.rollback()
            return
        document.lease_token = None
        document.lease_expires_at = None
        document.processed_at = now
        session.commit()


def _record_failure(
    session_factory: sessionmaker[Session],
    lease: DocumentLease,
    *,
    error_code: str,
    retryable: bool,
) -> None:
    with session_factory() as session:
        parents_active = _lock_parents(session, lease)
        document = session.scalar(
            select(Document)
            .where(
                Document.id == lease.document_id,
                Document.status == "processing",
                Document.lease_token == lease.token,
            )
            .with_for_update()
        )
        if document is None:
            return
        now = datetime.now(timezone.utc)
        if _lease_is_expired(document.lease_expires_at, now):
            return
        if not parents_active:
            _revoke_lease(document)
            session.commit()
            return

        should_retry = retryable and document.attempts_used < MAX_PROCESSING_ATTEMPTS
        document.status = "queued" if should_retry else "failed"
        document.retryable = should_retry
        document.error_code = error_code
        document.next_attempt_at = (
            now
            + timedelta(
                seconds=min(
                    get_settings().document_retry_base_seconds
                    * 2 ** (document.attempts_used - 1),
                    get_settings().document_retry_max_seconds,
                )
            )
            if should_retry
            else None
        )
        document.lease_token = None
        document.lease_expires_at = None
        document.processed_at = None if should_retry else now
        session.commit()


def process_next_document(session_factory: sessionmaker[Session]) -> bool:
    lease = _claim_document(session_factory)
    if lease is None:
        return False
    settings = get_settings()
    try:
        pages, has_text = extract_pdf_pages(
            storage_path(lease.storage_key),
            max_pages=settings.document_max_pages,
            max_text_bytes=settings.document_max_extracted_text_bytes,
            heartbeat=lambda: _renew_lease(session_factory, lease),
        )
    except StaleDocumentLease:
        return True
    except PermanentPdfError as error:
        _record_failure(session_factory, lease, error_code=error.error_code, retryable=False)
    except TransientPdfError as error:
        _record_failure(session_factory, lease, error_code=error.error_code, retryable=True)
    except Exception:
        logger.exception("Unexpected document processing failure")
        _record_failure(
            session_factory,
            lease,
            error_code="processing_error",
            retryable=True,
        )
    else:
        try:
            _publish_result(session_factory, lease, pages, has_text)
        except Exception:
            logger.exception("Could not atomically hand off extracted document for indexing")
    return True


def reconcile_orphaned_files(session_factory: sessionmaker[Session]) -> None:
    root = get_settings().document_storage_path
    if not root.exists():
        return
    with session_factory() as session:
        referenced_keys = set(session.scalars(select(Document.storage_key)))
        session.execute(
            delete(DocumentUploadAttempt).where(
                DocumentUploadAttempt.attempted_at
                < datetime.now(timezone.utc) - timedelta(days=1)
            )
        )
        session.commit()

    older_than = time.time() - ORPHAN_GRACE_PERIOD.total_seconds()
    for path in root.iterdir():
        try:
            if path.name.startswith(".upload-"):
                should_remove = path.stat().st_mtime < older_than
            elif _STORAGE_FILENAME.fullmatch(path.name):
                should_remove = (
                    path.name not in referenced_keys and path.stat().st_mtime < older_than
                )
            else:
                continue
        except OSError:
            logger.exception("Could not inspect private storage entry")
            continue
        if should_remove:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.exception("Could not remove orphaned private document file")


def run_worker(
    session_factory: sessionmaker[Session],
    *,
    poll_interval_seconds: float = 1.0,
    run_once: bool = False,
) -> None:
    reconcile_orphaned_files(session_factory)
    last_cleanup = time.monotonic()
    last_ai_recovery = time.monotonic()
    work_cursor = 0
    while True:
        try:
            work = (
                process_next_document,
                process_next_index_job,
                process_next_study_material_job,
            )
            processed = False
            for offset in range(len(work)):
                process = work[(work_cursor + offset) % len(work)]
                if process(session_factory):
                    processed = True
                    work_cursor = (work_cursor + offset + 1) % len(work)
                    break
        except SQLAlchemyError:
            logger.exception("Document worker database operation failed")
            processed = False
        if run_once:
            return
        if time.monotonic() - last_cleanup >= ORPHAN_CLEANUP_INTERVAL.total_seconds():
            try:
                reconcile_orphaned_files(session_factory)
            except SQLAlchemyError:
                logger.exception("Document storage reconciliation failed")
            last_cleanup = time.monotonic()
        if time.monotonic() - last_ai_recovery >= AI_RECOVERY_INTERVAL.total_seconds():
            try:
                reconcile_missing_indexes(session_factory)
                recover_expired_reservations(session_factory)
                recover_expired_study_material_jobs(session_factory)
            except SQLAlchemyError:
                logger.exception("AI indexing or usage reconciliation failed")
            last_ai_recovery = time.monotonic()
        if not processed:
            time.sleep(poll_interval_seconds)
