import logging
import random
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, or_, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from app.ai.openai_provider import OpenAIProvider
from app.ai.provider import AIProviderError
from app.core.config import Settings, get_settings
from app.models import (
    AIEmbeddingProfile,
    Document,
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentIndexGeneration,
    DocumentIndexHead,
    DocumentIndexJob,
    DocumentPage,
    StudySpace,
    Subject,
)
from app.services.ai_usage_reservations import (
    UsageLimitExceeded,
    mark_dispatching,
    mark_uncertain,
    release_known_nonbillable,
    reserve_usage,
    settle_usage,
)
from app.services.chunking import DocumentChunk as ChunkValue
from app.services.chunking import chunk_document_pages

logger = logging.getLogger(__name__)
CHUNKING_VERSION = "page-aware-utf8-byte-overlap-v1"
TOKEN_COUNT_STRATEGY = "utf8-byte-upper-bound-v1"
MAX_INDEX_ATTEMPTS = 5


@dataclass(frozen=True)
class IndexLease:
    job_id: UUID
    document_id: UUID
    generation_id: UUID
    user_id: UUID
    token: UUID
    attempts_used: int


def _active_profile(session: Session, settings: Settings) -> AIEmbeddingProfile:
    profile = session.scalar(
        select(AIEmbeddingProfile)
        .where(AIEmbeddingProfile.singleton_key.is_(True))
        .with_for_update(read=True)
    )
    if profile is None:
        raise RuntimeError("AI embedding profile is not initialized")
    if (
        profile.profile_id != settings.ai_embedding_profile_id
        or profile.provider != "openai"
        or profile.model_name != settings.ai_embedding_model
        or profile.dimensions != settings.ai_embedding_dimensions
        or profile.distance_metric != "cosine"
    ):
        raise RuntimeError("AI embedding profile configuration does not match the database")
    return profile


def ensure_index_generation(
    session: Session,
    document: Document,
    *,
    settings: Settings | None = None,
) -> DocumentIndexGeneration | None:
    if document.status != "completed" or not document.has_extractable_text:
        return None
    settings = settings or get_settings()
    profile = _active_profile(session, settings)
    generation = session.scalar(
        select(DocumentIndexGeneration)
        .where(
            DocumentIndexGeneration.document_id == document.id,
            DocumentIndexGeneration.source_document_sha256 == document.content_sha256,
            DocumentIndexGeneration.embedding_profile_id == profile.profile_id,
            DocumentIndexGeneration.chunking_version == CHUNKING_VERSION,
        )
        .with_for_update()
    )
    if generation is None:
        generation_number = (
            session.scalar(
                select(func.max(DocumentIndexGeneration.generation_number)).where(
                    DocumentIndexGeneration.document_id == document.id
                )
            )
            or 0
        ) + 1
        generation = DocumentIndexGeneration(
            id=uuid4(),
            document_id=document.id,
            generation_number=generation_number,
            source_document_sha256=document.content_sha256,
            embedding_profile_id=profile.profile_id,
            model_name=profile.model_name,
            dimensions=profile.dimensions,
            distance_metric=profile.distance_metric,
            chunking_version=CHUNKING_VERSION,
            token_count_strategy=TOKEN_COUNT_STRATEGY,
            status="building",
        )
        session.add(generation)
        session.flush()
    job = session.scalar(
        select(DocumentIndexJob)
        .where(DocumentIndexJob.generation_id == generation.id)
        .with_for_update()
    )
    if job is None:
        session.add(
            DocumentIndexJob(
                id=uuid4(),
                document_id=document.id,
                generation_id=generation.id,
                status="queued",
                attempts_used=0,
            )
        )
    elif job.status == "failed" and generation.status != "ready":
        job.status = "queued"
        job.attempts_used = 0
        job.next_attempt_at = None
        job.lease_token = None
        job.lease_expires_at = None
        job.error_code = None
        generation.status = "building"
        generation.error_code = None
    return generation


def reconcile_missing_indexes(
    session_factory: sessionmaker[Session], *, limit: int = 50
) -> int:
    if not 1 <= limit <= 500:
        raise ValueError("Reconciliation batch limit is invalid")
    settings = get_settings()
    if not settings.ai_enabled:
        return 0
    enqueued = 0
    with session_factory() as session:
        _active_profile(session, settings)
        current_ready_generation_exists = (
            select(DocumentIndexGeneration.id)
            .where(
                DocumentIndexGeneration.document_id == Document.id,
                DocumentIndexGeneration.source_document_sha256 == Document.content_sha256,
                DocumentIndexGeneration.embedding_profile_id
                == settings.ai_embedding_profile_id,
                DocumentIndexGeneration.chunking_version == CHUNKING_VERSION,
                DocumentIndexGeneration.status == "ready",
            )
            .exists()
        )
        documents = list(
            session.scalars(
                select(Document)
                .join(Subject, Subject.id == Document.subject_id)
                .join(StudySpace, StudySpace.id == Document.study_space_id)
                .where(
                    Document.status == "completed",
                    Document.has_extractable_text.is_(True),
                    Subject.archived_at.is_(None),
                    StudySpace.archived_at.is_(None),
                    ~current_ready_generation_exists,
                )
                .order_by(Document.processed_at, Document.id)
                .limit(limit)
                .with_for_update(skip_locked=True, of=Document)
            )
        )
        for document in documents:
            existed = session.scalar(
                select(DocumentIndexGeneration.id).where(
                    DocumentIndexGeneration.document_id == document.id,
                    DocumentIndexGeneration.source_document_sha256 == document.content_sha256,
                    DocumentIndexGeneration.embedding_profile_id
                    == settings.ai_embedding_profile_id,
                    DocumentIndexGeneration.chunking_version == CHUNKING_VERSION,
                )
            )
            generation = ensure_index_generation(session, document, settings=settings)
            if generation is not None and existed is None:
                enqueued += 1
        session.commit()
    return enqueued


def _claim_index_job(session_factory: sessionmaker[Session]) -> IndexLease | None:
    settings = get_settings()
    if not settings.ai_enabled:
        return None
    now = datetime.now(timezone.utc)
    eligible = or_(
        and_(
            DocumentIndexJob.status == "queued",
            or_(
                DocumentIndexJob.next_attempt_at.is_(None),
                DocumentIndexJob.next_attempt_at <= now,
            ),
        ),
        and_(
            DocumentIndexJob.status == "processing",
            or_(
                DocumentIndexJob.lease_expires_at.is_(None),
                DocumentIndexJob.lease_expires_at <= now,
            ),
        ),
    )
    with session_factory() as session:
        job_id = session.scalar(
            select(DocumentIndexJob.id)
            .join(Document, Document.id == DocumentIndexJob.document_id)
            .join(Subject, Subject.id == Document.subject_id)
            .join(StudySpace, StudySpace.id == Document.study_space_id)
            .where(
                eligible,
                Subject.archived_at.is_(None),
                StudySpace.archived_at.is_(None),
            )
            .order_by(DocumentIndexJob.created_at, DocumentIndexJob.id)
            .limit(1)
            .with_for_update(skip_locked=True, of=DocumentIndexJob)
        )
        if job_id is None:
            return None
        job = session.scalar(
            select(DocumentIndexJob)
            .where(DocumentIndexJob.id == job_id)
            .with_for_update()
        )
        if job is None:
            return None
        if job.attempts_used >= MAX_INDEX_ATTEMPTS:
            job.status = "failed"
            job.error_code = "attempts_exhausted"
            job.lease_token = None
            job.lease_expires_at = None
            generation = session.get(DocumentIndexGeneration, job.generation_id)
            if generation is not None:
                generation.status = "failed"
                generation.error_code = "attempts_exhausted"
            session.commit()
            return None

        document = session.get(Document, job.document_id)
        if document is None:
            return None
        token = uuid4()
        job.status = "processing"
        job.attempts_used += 1
        job.next_attempt_at = None
        job.lease_token = token
        job.lease_expires_at = now + timedelta(seconds=settings.document_worker_lease_seconds)
        job.error_code = None
        session.commit()
        return IndexLease(
            job_id=job.id,
            document_id=job.document_id,
            generation_id=job.generation_id,
            user_id=document.user_id,
            token=token,
            attempts_used=job.attempts_used,
        )


def _lease_is_current(session: Session, lease: IndexLease) -> bool:
    return (
        session.scalar(
            select(DocumentIndexJob.id)
            .join(Document, Document.id == DocumentIndexJob.document_id)
            .join(Subject, Subject.id == Document.subject_id)
            .join(StudySpace, StudySpace.id == Document.study_space_id)
            .where(
                DocumentIndexJob.id == lease.job_id,
                DocumentIndexJob.status == "processing",
                DocumentIndexJob.lease_token == lease.token,
                DocumentIndexJob.lease_expires_at > datetime.now(timezone.utc),
                Document.id == lease.document_id,
                Document.user_id == lease.user_id,
                Document.status == "completed",
                Document.has_extractable_text.is_(True),
                Subject.archived_at.is_(None),
                StudySpace.archived_at.is_(None),
            )
        )
        is not None
    )


def _prepare_chunks(
    session_factory: sessionmaker[Session], lease: IndexLease, settings: Settings
) -> list[ChunkValue]:
    with session_factory() as session:
        if not _lease_is_current(session, lease):
            return []
        generation = session.get(DocumentIndexGeneration, lease.generation_id)
        if generation is None:
            return []
        if generation.source_document_sha256 != session.get(
            Document, lease.document_id
        ).content_sha256:
            raise RuntimeError("Index generation source document changed")
        pages = list(
            session.execute(
                select(DocumentPage.page_number, DocumentPage.extracted_text)
                .where(DocumentPage.document_id == lease.document_id)
                .order_by(DocumentPage.page_number)
            ).all()
        )
        page_values = [(page_number, content) for page_number, content in pages]
        chunks = chunk_document_pages(
            page_values,
            target_bytes=settings.ai_chunk_target_utf8_bytes,
            max_bytes=settings.ai_chunk_max_utf8_bytes,
            overlap_bytes=settings.ai_chunk_overlap_utf8_bytes,
        )
        existing = list(
            session.scalars(
                select(DocumentChunk)
                .where(DocumentChunk.generation_id == generation.id)
                .order_by(DocumentChunk.chunk_index)
            )
        )
        if existing:
            persisted_signature = [
                (item.chunk_index, item.page_start, item.page_end, item.content_sha256)
                for item in existing
            ]
            expected_signature = [
                (item.chunk_index, item.page_start, item.page_end, item.content_sha256)
                for item in chunks
            ]
            if persisted_signature != expected_signature:
                raise RuntimeError("Persisted chunks do not match the generation identity")
        else:
            session.add_all(
                [
                    DocumentChunk(
                        document_id=lease.document_id,
                        generation_id=generation.id,
                        chunk_index=chunk.chunk_index,
                        page_start=chunk.page_start,
                        page_end=chunk.page_end,
                        text=chunk.text,
                        utf8_byte_upper_bound=chunk.utf8_byte_upper_bound,
                        content_sha256=chunk.content_sha256,
                    )
                    for chunk in chunks
                ]
            )
            session.commit()
        return chunks


def _batch_chunks(chunks: list[ChunkValue], settings: Settings) -> list[list[ChunkValue]]:
    batches: list[list[ChunkValue]] = []
    current: list[ChunkValue] = []
    byte_total = 0
    for chunk in chunks:
        if chunk.utf8_byte_upper_bound > settings.ai_chunk_max_utf8_bytes:
            raise ValueError("Chunk exceeds the configured UTF-8 byte upper bound")
        if current and (
            len(current) >= settings.ai_embedding_batch_max_chunks
            or byte_total + chunk.utf8_byte_upper_bound
            > settings.ai_embedding_batch_max_utf8_bytes
        ):
            batches.append(current)
            current = []
            byte_total = 0
        current.append(chunk)
        byte_total += chunk.utf8_byte_upper_bound
    if current:
        batches.append(current)
    return batches


def _embed_batch(
    session_factory: sessionmaker[Session],
    lease: IndexLease,
    batch: list[ChunkValue],
    provider: OpenAIProvider,
    settings: Settings,
) -> None:
    with session_factory() as session:
        existing_indexes = set(
            session.scalars(
                select(DocumentChunkEmbedding.chunk_index).where(
                    DocumentChunkEmbedding.generation_id == lease.generation_id,
                    DocumentChunkEmbedding.chunk_index.in_(
                        [chunk.chunk_index for chunk in batch]
                    ),
                )
            )
        )
    pending = [chunk for chunk in batch if chunk.chunk_index not in existing_indexes]
    if not pending:
        return
    input_upper_bound = sum(chunk.utf8_byte_upper_bound for chunk in pending)
    if input_upper_bound > settings.ai_embedding_batch_max_utf8_bytes:
        raise ValueError("Embedding batch exceeds its configured input bound")
    with session_factory() as session:
        if not _lease_is_current(session, lease):
            return
        reservation = reserve_usage(
            session,
            user_id=lease.user_id,
            document_id=lease.document_id,
            operation="document_embedding",
            idempotency_key=f"index:{lease.job_id}:{uuid4()}",
            input_token_upper_bound=input_upper_bound,
            settings=settings,
        )
        mark_dispatching(session, reservation.id)
    try:
        result = provider.embed([chunk.text for chunk in pending])
    except AIProviderError as error:
        with session_factory() as session:
            if error.error_code == "provider_request_rejected":
                release_known_nonbillable(session, reservation.id)
            else:
                mark_uncertain(session, reservation.id)
        raise
    except Exception:
        with session_factory() as session:
            mark_uncertain(session, reservation.id)
        raise

    with session_factory() as session:
        settle_usage(
            session,
            reservation.id,
            input_tokens=result.input_tokens,
            provider_request_id=result.provider_request_id,
            conservative_estimate=result.input_tokens is None,
            settings=settings,
        )
        if not _lease_is_current(session, lease):
            return
        profile = _active_profile(session, settings)
        existing_indexes = set(
            session.scalars(
                select(DocumentChunkEmbedding.chunk_index).where(
                    DocumentChunkEmbedding.generation_id == lease.generation_id
                )
            )
        )
        session.add_all(
            [
                DocumentChunkEmbedding(
                    document_id=lease.document_id,
                    generation_id=lease.generation_id,
                    chunk_index=chunk.chunk_index,
                    profile_id=profile.profile_id,
                    embedding=list(vector),
                )
                for chunk, vector in zip(pending, result.vectors, strict=True)
                if chunk.chunk_index not in existing_indexes
            ]
        )
        session.commit()


def _activate_generation(
    session_factory: sessionmaker[Session], lease: IndexLease, expected_count: int
) -> bool:
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        job = session.scalar(
            select(DocumentIndexJob)
            .where(DocumentIndexJob.id == lease.job_id)
            .with_for_update()
        )
        if (
            job is None
            or job.status != "processing"
            or job.lease_token != lease.token
            or job.lease_expires_at is None
            or job.lease_expires_at <= now
        ):
            return False
        document = session.scalar(
            select(Document)
            .where(
                Document.id == lease.document_id,
                Document.user_id == lease.user_id,
                Document.status == "completed",
                Document.has_extractable_text.is_(True),
            )
            .with_for_update()
        )
        if document is None:
            return False
        subject = session.scalar(
            select(Subject)
            .where(
                Subject.id == document.subject_id,
                Subject.user_id == document.user_id,
                Subject.archived_at.is_(None),
            )
            .with_for_update()
        )
        study_space = session.scalar(
            select(StudySpace)
            .where(
                StudySpace.id == document.study_space_id,
                StudySpace.subject_id == document.subject_id,
                StudySpace.archived_at.is_(None),
            )
            .with_for_update()
        )
        generation = session.scalar(
            select(DocumentIndexGeneration)
            .where(DocumentIndexGeneration.id == lease.generation_id)
            .with_for_update()
        )
        if (
            subject is None
            or study_space is None
            or generation is None
            or generation.source_document_sha256 != document.content_sha256
        ):
            return False
        actual_count = session.scalar(
            select(func.count()).select_from(DocumentChunkEmbedding).where(
                DocumentChunkEmbedding.generation_id == generation.id
            )
        )
        chunk_count = session.scalar(
            select(func.count()).select_from(DocumentChunk).where(
                DocumentChunk.generation_id == generation.id
            )
        )
        if actual_count != expected_count or chunk_count != expected_count:
            raise RuntimeError("Index generation is incomplete")
        settings = get_settings()
        profile = _active_profile(session, settings)
        if generation.embedding_profile_id != profile.profile_id:
            return False
        latest_generation = session.scalar(
            select(func.max(DocumentIndexGeneration.generation_number)).where(
                DocumentIndexGeneration.document_id == document.id
            )
        )
        if generation.generation_number != latest_generation:
            return False

        generation.status = "ready"
        generation.ready_at = now
        generation.error_code = None
        head = session.get(DocumentIndexHead, document.id, with_for_update=True)
        if head is None:
            session.add(
                DocumentIndexHead(
                    document_id=document.id,
                    profile_id=profile.profile_id,
                    generation_id=generation.id,
                )
            )
        else:
            head.profile_id = profile.profile_id
            head.generation_id = generation.id
            head.updated_at = now
        job.status = "completed"
        job.completed_at = now
        job.lease_token = None
        job.lease_expires_at = None
        session.commit()
        return True


def _record_index_failure(
    session_factory: sessionmaker[Session],
    lease: IndexLease,
    error_code: str,
    *,
    retryable: bool,
) -> None:
    now = datetime.now(timezone.utc)
    settings = get_settings()
    with session_factory() as session:
        job = session.scalar(
            select(DocumentIndexJob)
            .where(
                DocumentIndexJob.id == lease.job_id,
                DocumentIndexJob.status == "processing",
                DocumentIndexJob.lease_token == lease.token,
            )
            .with_for_update()
        )
        if job is None:
            return
        should_retry = retryable and job.attempts_used < MAX_INDEX_ATTEMPTS
        job.status = "queued" if should_retry else "failed"
        job.error_code = error_code
        job.next_attempt_at = (
            now
            + timedelta(
                seconds=min(
                    settings.document_retry_base_seconds * (2 ** (job.attempts_used - 1)),
                    settings.document_retry_max_seconds,
                )
                * random.uniform(0.8, 1.2)
            )
            if should_retry
            else None
        )
        job.lease_token = None
        job.lease_expires_at = None
        generation = session.scalar(
            select(DocumentIndexGeneration)
            .where(DocumentIndexGeneration.id == job.generation_id)
            .with_for_update()
        )
        if generation is not None and not should_retry:
            generation.status = "failed"
            generation.error_code = error_code
        session.commit()


def process_next_index_job(
    session_factory: sessionmaker[Session],
    *,
    provider_factory=OpenAIProvider,
) -> bool:
    lease = _claim_index_job(session_factory)
    if lease is None:
        return False
    settings = get_settings()
    try:
        chunks = _prepare_chunks(session_factory, lease, settings)
        if not chunks and not _lease_still_current(session_factory, lease):
            return True
        if not chunks:
            raise ValueError("Completed document has no non-empty index chunks")
        provider = provider_factory(settings)
        for batch in _batch_chunks(chunks, settings):
            _embed_batch(session_factory, lease, batch, provider, settings)
            with session_factory() as session:
                if not _lease_is_current(session, lease):
                    return True
                job = session.get(DocumentIndexJob, lease.job_id)
                if job is None or job.lease_token != lease.token:
                    return True
                job.lease_expires_at = datetime.now(timezone.utc) + timedelta(
                    seconds=settings.document_worker_lease_seconds
                )
                session.commit()
        _activate_generation(session_factory, lease, len(chunks))
    except UsageLimitExceeded:
        _defer_index_job(session_factory, lease)
    except AIProviderError as error:
        _record_index_failure(
            session_factory, lease, error.error_code, retryable=error.retryable
        )
    except (RuntimeError, ValueError):
        logger.exception("Document indexing failed validation")
        _record_index_failure(session_factory, lease, "indexing_failed", retryable=False)
    except SQLAlchemyError:
        logger.exception("Document indexing database operation failed")
        _record_index_failure(session_factory, lease, "database_error", retryable=True)
    except Exception:
        logger.exception("Unexpected document indexing failure")
        _record_index_failure(session_factory, lease, "indexing_error", retryable=True)
    return True


def _lease_still_current(session_factory: sessionmaker[Session], lease: IndexLease) -> bool:
    with session_factory() as session:
        return _lease_is_current(session, lease)


def _defer_index_job(session_factory: sessionmaker[Session], lease: IndexLease) -> None:
    settings = get_settings()
    now = datetime.now(timezone.utc)
    with session_factory() as session:
        job = session.scalar(
            select(DocumentIndexJob)
            .where(
                DocumentIndexJob.id == lease.job_id,
                DocumentIndexJob.status == "processing",
                DocumentIndexJob.lease_token == lease.token,
            )
            .with_for_update()
        )
        if job is None:
            return
        job.status = "queued"
        job.attempts_used = max(0, job.attempts_used - 1)
        job.error_code = "usage_limit_deferred"
        job.next_attempt_at = now + timedelta(minutes=5)
        job.lease_token = None
        job.lease_expires_at = None
        session.commit()
