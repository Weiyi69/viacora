"""Internal study-material lifecycle and cleanup operations.

Mutations that span the hierarchy follow subject -> study space -> document ->
index head -> index generation -> index job -> kit -> study-material job.
Operations may skip unneeded rows; claim and recovery lock only
study-material jobs and commit before any operation acquires source or
hierarchy locks.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
from uuid import UUID, uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.models import (
    Document,
    DocumentChunk,
    DocumentIndexGeneration,
    DocumentIndexHead,
    DocumentIndexJob,
    StudyKit,
    StudyMaterial,
    StudyMaterialCitation,
    StudyMaterialJob,
    StudyMaterialJobSource,
    StudySpace,
    Subject,
)
from app.services.document_indexing import CHUNKING_VERSION

MAX_STUDY_MATERIAL_ATTEMPTS = 5


@dataclass(frozen=True)
class StudyMaterialLease:
    job_id: UUID
    user_id: UUID
    study_space_id: UUID
    study_kit_id: UUID
    token: UUID
    attempts_used: int


@dataclass(frozen=True)
class StudyMaterialSource:
    document_id: UUID
    generation_id: UUID


@dataclass(frozen=True)
class StudyMaterialCitationInput:
    citation_key: str
    document_id: UUID
    generation_id: UUID
    chunk_index: int


class IdempotencyConflict(ValueError):
    pass


def _request_hash(
    *,
    user_id: UUID,
    study_space_id: UUID,
    study_kit_id: UUID,
    kind: str,
    payload: dict[str, Any],
    sources: Sequence[tuple[UUID, UUID, str]],
) -> str:
    serialized = json.dumps(
        {
            "user_id": str(user_id),
            "study_space_id": str(study_space_id),
            "study_kit_id": str(study_kit_id),
            "kind": kind,
            "payload": payload,
            "sources": [
                (str(document_id), str(generation_id), source_hash)
                for document_id, generation_id, source_hash in sorted(
                    sources, key=lambda source: (str(source[0]), str(source[1]), source[2])
                )
            ],
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(serialized).hexdigest()


def _retry_delay_seconds(settings: Settings, attempts_used: int) -> int:
    return min(
        settings.document_retry_base_seconds * 2 ** max(0, attempts_used - 1),
        settings.document_retry_max_seconds,
    )


def _lock_source_documents(
    session: Session, document_ids: Sequence[UUID]
) -> list[Document]:
    ordered_ids = sorted(set(document_ids), key=str)
    if not ordered_ids:
        return []
    return list(
        session.scalars(
            select(Document)
            .where(Document.id.in_(ordered_ids))
            .order_by(Document.id)
            .with_for_update(of=Document)
        )
    )


def _lock_index_heads(session: Session, document_ids: Sequence[UUID]) -> dict[UUID, DocumentIndexHead]:
    ordered_ids = sorted(set(document_ids), key=str)
    if not ordered_ids:
        return {}
    heads = session.scalars(
        select(DocumentIndexHead)
        .where(DocumentIndexHead.document_id.in_(ordered_ids))
        .order_by(DocumentIndexHead.document_id)
        .with_for_update()
    )
    return {head.document_id: head for head in heads}


def _lock_document_index_generations_and_jobs(
    session: Session, document_ids: Sequence[UUID]
) -> None:
    ordered_ids = sorted(set(document_ids), key=str)
    if not ordered_ids:
        return
    list(
        session.scalars(
            select(DocumentIndexGeneration)
            .where(DocumentIndexGeneration.document_id.in_(ordered_ids))
            .order_by(DocumentIndexGeneration.document_id, DocumentIndexGeneration.id)
            .with_for_update(of=DocumentIndexGeneration)
        )
    )
    list(
        session.scalars(
            select(DocumentIndexJob)
            .join(
                DocumentIndexGeneration,
                DocumentIndexGeneration.id == DocumentIndexJob.generation_id,
            )
            .where(DocumentIndexGeneration.document_id.in_(ordered_ids))
            .order_by(DocumentIndexGeneration.document_id, DocumentIndexJob.id)
            .with_for_update(of=DocumentIndexJob)
        )
    )


def _active_source_selection(
    session: Session,
    *,
    user_id: UUID,
    study_space_id: UUID,
    documents: Sequence[Document],
    heads: dict[UUID, DocumentIndexHead],
    sources: Sequence[StudyMaterialSource],
) -> list[tuple[UUID, UUID, str]]:
    settings = get_settings()
    source_by_document = {source.document_id: source for source in sources}
    if len(source_by_document) != len(sources) or len(documents) != len(sources):
        raise ValueError("Study material sources are invalid")

    hashes: list[tuple[UUID, UUID, str]] = []
    for document in documents:
        source = source_by_document.get(document.id)
        head = heads.get(document.id)
        if (
            source is None
            or document.user_id != user_id
            or document.study_space_id != study_space_id
            or document.status != "completed"
            or not document.has_extractable_text
            or head is None
            or head.generation_id != source.generation_id
            or head.profile_id != settings.ai_embedding_profile_id
        ):
            raise ValueError("A selected source is no longer available")

        generation = session.scalar(
            select(DocumentIndexGeneration).where(
                DocumentIndexGeneration.id == source.generation_id,
                DocumentIndexGeneration.document_id == document.id,
                DocumentIndexGeneration.status == "ready",
                DocumentIndexGeneration.embedding_profile_id == head.profile_id,
                DocumentIndexGeneration.source_document_sha256 == document.content_sha256,
                DocumentIndexGeneration.chunking_version == CHUNKING_VERSION,
            )
        )
        if generation is None:
            raise ValueError("A selected source index is no longer current")
        hashes.append((document.id, generation.id, document.content_sha256))
    return hashes


def enqueue_study_material_job(
    session: Session,
    *,
    user_id: UUID,
    study_kit_id: UUID,
    idempotency_key: str,
    request_payload: dict[str, Any],
    sources: Sequence[StudyMaterialSource],
) -> StudyMaterialJob:
    if not idempotency_key or len(idempotency_key) > 160:
        raise ValueError("Idempotency key is invalid")
    if not sources:
        raise ValueError("At least one indexed source is required")

    kit = session.scalar(
        select(StudyKit).where(
            StudyKit.id == study_kit_id,
            StudyKit.user_id == user_id,
            StudyKit.archived_at.is_(None),
        )
    )
    if kit is None:
        raise ValueError("Study kit is unavailable")

    documents = _lock_source_documents(session, [source.document_id for source in sources])
    heads = _lock_index_heads(session, [document.id for document in documents])
    kit = session.scalar(
        select(StudyKit)
        .where(
            StudyKit.id == study_kit_id,
            StudyKit.user_id == user_id,
            StudyKit.archived_at.is_(None),
        )
        .with_for_update()
    )
    if kit is None:
        raise ValueError("Study kit is unavailable")
    study_space = session.scalar(
        select(StudySpace).where(
            StudySpace.id == kit.study_space_id,
            StudySpace.subject_id == kit.subject_id,
            StudySpace.archived_at.is_(None),
        )
    )
    subject = session.scalar(
        select(Subject).where(
            Subject.id == kit.subject_id,
            Subject.user_id == user_id,
            Subject.archived_at.is_(None),
        )
    )
    if study_space is None or subject is None:
        raise ValueError("Study kit is unavailable")

    source_hashes = _active_source_selection(
        session,
        user_id=user_id,
        study_space_id=kit.study_space_id,
        documents=documents,
        heads=heads,
        sources=sources,
    )
    request_hash = _request_hash(
        user_id=user_id,
        study_space_id=kit.study_space_id,
        study_kit_id=kit.id,
        kind="notes",
        payload=request_payload,
        sources=source_hashes,
    )
    existing = session.scalar(
        select(StudyMaterialJob).where(
            StudyMaterialJob.user_id == user_id,
            StudyMaterialJob.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_hash != request_hash:
            raise IdempotencyConflict("Idempotency key was already used for another request")
        return existing

    job = StudyMaterialJob(
        id=uuid4(),
        user_id=user_id,
        study_space_id=kit.study_space_id,
        study_kit_id=kit.id,
        kind="notes",
        status="queued",
        attempts_used=0,
        idempotency_key=idempotency_key,
        request_hash=request_hash,
        request_payload=request_payload,
    )
    try:
        with session.begin_nested():
            session.add(job)
            for document_id, generation_id, source_hash in source_hashes:
                session.add(
                    StudyMaterialJobSource(
                        job_id=job.id,
                        document_id=document_id,
                        user_id=user_id,
                        study_space_id=kit.study_space_id,
                        generation_id=generation_id,
                        source_document_sha256=source_hash,
                    )
                )
            session.flush()
    except IntegrityError:
        existing = session.scalar(
            select(StudyMaterialJob).where(
                StudyMaterialJob.user_id == user_id,
                StudyMaterialJob.idempotency_key == idempotency_key,
            )
        )
        if existing is not None and existing.request_hash == request_hash:
            return existing
        if existing is not None:
            raise IdempotencyConflict(
                "Idempotency key was already used for another request"
            )
        raise
    return job


def claim_next_study_material_job(
    session_factory: sessionmaker[Session], *, now: datetime | None = None
) -> StudyMaterialLease | None:
    settings = get_settings()
    current_time = now or datetime.now(timezone.utc)
    with session_factory() as session:
        while True:
            job = session.scalar(
                select(StudyMaterialJob)
                .join(StudyKit, StudyKit.id == StudyMaterialJob.study_kit_id)
                .join(Subject, Subject.id == StudyKit.subject_id)
                .join(StudySpace, StudySpace.id == StudyKit.study_space_id)
                .where(
                    StudyMaterialJob.status == "queued",
                    or_(
                        StudyMaterialJob.next_attempt_at.is_(None),
                        StudyMaterialJob.next_attempt_at <= current_time,
                    ),
                    StudyKit.archived_at.is_(None),
                    Subject.archived_at.is_(None),
                    StudySpace.archived_at.is_(None),
                )
                .order_by(StudyMaterialJob.created_at, StudyMaterialJob.id)
                .limit(1)
                .with_for_update(skip_locked=True, of=StudyMaterialJob)
            )
            if job is None:
                return None
            if job.attempts_used >= MAX_STUDY_MATERIAL_ATTEMPTS:
                job.status = "failed"
                job.error_code = "attempts_exhausted"
                job.lease_token = None
                job.lease_expires_at = None
                job.next_attempt_at = None
                job.completed_at = current_time
                session.commit()
                continue

            token = uuid4()
            job.status = "processing"
            job.attempts_used += 1
            job.started_at = job.started_at or current_time
            job.next_attempt_at = None
            job.lease_token = token
            job.lease_expires_at = current_time + timedelta(
                seconds=settings.document_worker_lease_seconds
            )
            job.cancel_requested_at = None
            job.error_code = None
            lease = StudyMaterialLease(
                job_id=job.id,
                user_id=job.user_id,
                study_space_id=job.study_space_id,
                study_kit_id=job.study_kit_id,
                token=token,
                attempts_used=job.attempts_used,
            )
            session.commit()
            return lease


def renew_study_material_lease(
    session_factory: sessionmaker[Session],
    lease: StudyMaterialLease,
    *,
    now: datetime | None = None,
) -> bool:
    with session_factory() as session:
        job = session.scalar(
            select(StudyMaterialJob)
            .where(
                StudyMaterialJob.id == lease.job_id,
                StudyMaterialJob.status == "processing",
                StudyMaterialJob.lease_token == lease.token,
                StudyMaterialJob.cancel_requested_at.is_(None),
            )
            .with_for_update()
        )
        current_time = now or datetime.now(timezone.utc)
        if (
            job is None
            or job.lease_expires_at is None
            or job.lease_expires_at <= current_time
        ):
            return False
        job.lease_expires_at = current_time + timedelta(
            seconds=get_settings().document_worker_lease_seconds
        )
        session.commit()
        return True


def recover_expired_study_material_jobs(
    session_factory: sessionmaker[Session],
    *,
    limit: int = 100,
    now: datetime | None = None,
) -> int:
    if not 1 <= limit <= 500:
        raise ValueError("Recovery batch limit is invalid")
    current_time = now or datetime.now(timezone.utc)
    recovered = 0
    with session_factory() as session:
        jobs = list(
            session.scalars(
                select(StudyMaterialJob)
                .where(
                    StudyMaterialJob.status == "processing",
                    or_(
                        StudyMaterialJob.lease_expires_at.is_(None),
                        StudyMaterialJob.lease_expires_at <= current_time,
                    ),
                )
                .order_by(StudyMaterialJob.created_at, StudyMaterialJob.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        )
        for job in jobs:
            job.lease_token = None
            job.lease_expires_at = None
            job.cancel_requested_at = None
            if job.attempts_used >= MAX_STUDY_MATERIAL_ATTEMPTS:
                job.status = "failed"
                job.error_code = "attempts_exhausted"
                job.completed_at = current_time
            else:
                job.status = "queued"
                job.error_code = "lease_expired"
                job.next_attempt_at = current_time + timedelta(
                    seconds=_retry_delay_seconds(
                        get_settings(), job.attempts_used
                    )
                )
            recovered += 1
        session.commit()
    return recovered


def fail_study_material_job(
    session_factory: sessionmaker[Session],
    lease: StudyMaterialLease,
    *,
    error_code: str,
    retryable: bool,
    now: datetime | None = None,
) -> bool:
    if not error_code or len(error_code) > 64:
        raise ValueError("Study material error code is invalid")
    with session_factory() as session:
        job = session.scalar(
            select(StudyMaterialJob)
            .where(
                StudyMaterialJob.id == lease.job_id,
                StudyMaterialJob.status == "processing",
                StudyMaterialJob.lease_token == lease.token,
                StudyMaterialJob.cancel_requested_at.is_(None),
            )
            .with_for_update()
        )
        current_time = now or datetime.now(timezone.utc)
        if (
            job is None
            or job.lease_expires_at is None
            or job.lease_expires_at <= current_time
        ):
            return False
        should_retry = retryable and job.attempts_used < MAX_STUDY_MATERIAL_ATTEMPTS
        job.status = "queued" if should_retry else "failed"
        job.error_code = error_code
        job.lease_token = None
        job.lease_expires_at = None
        if should_retry:
            job.next_attempt_at = current_time + timedelta(
                seconds=_retry_delay_seconds(
                    get_settings(), job.attempts_used
                )
            )
        else:
            job.next_attempt_at = None
            job.completed_at = current_time
        session.commit()
        return True


def cancel_study_material_job(
    session_factory: sessionmaker[Session],
    *,
    user_id: UUID,
    job_id: UUID,
    now: datetime | None = None,
) -> bool:
    with session_factory() as session:
        snapshot = session.scalar(
            select(StudyMaterialJob).where(
                StudyMaterialJob.id == job_id,
                StudyMaterialJob.user_id == user_id,
            )
        )
        if snapshot is None:
            return False
        source_document_ids = list(
            session.scalars(
                select(StudyMaterialJobSource.document_id)
                .where(StudyMaterialJobSource.job_id == job_id)
                .order_by(StudyMaterialJobSource.document_id)
            )
        )
        documents = _lock_source_documents(session, source_document_ids)
        _lock_index_heads(session, [document.id for document in documents])
        session.scalar(
            select(StudyKit)
            .where(StudyKit.id == snapshot.study_kit_id)
            .with_for_update()
        )
        job = session.scalar(
            select(StudyMaterialJob)
            .where(
                StudyMaterialJob.id == job_id,
                StudyMaterialJob.user_id == user_id,
            )
            .with_for_update()
        )
        if job is None or job.status in {"completed", "failed", "cancelled"}:
            session.rollback()
            return False
        current_time = now or datetime.now(timezone.utc)
        job.status = "cancelled"
        job.cancel_requested_at = current_time
        job.lease_token = None
        job.lease_expires_at = None
        job.next_attempt_at = None
        job.completed_at = current_time
        session.commit()
        return True


def cancel_jobs_selected_by_documents(
    session: Session,
    document_ids: Sequence[UUID],
    *,
    now: datetime | None = None,
) -> set[UUID]:
    current_time = now or datetime.now(timezone.utc)
    documents = _lock_source_documents(session, document_ids)
    ordered_ids = [document.id for document in documents]
    if not ordered_ids:
        return set()
    _lock_index_heads(session, ordered_ids)
    job_ids = list(
        session.scalars(
            select(StudyMaterialJob.id)
            .join(
                StudyMaterialJobSource,
                StudyMaterialJobSource.job_id == StudyMaterialJob.id,
            )
            .where(StudyMaterialJobSource.document_id.in_(ordered_ids))
            .distinct()
            .order_by(StudyMaterialJob.id)
        )
    )
    if not job_ids:
        return set()
    kit_ids = list(
        session.scalars(
            select(StudyMaterialJob.study_kit_id)
            .where(StudyMaterialJob.id.in_(job_ids))
            .distinct()
            .order_by(StudyMaterialJob.study_kit_id)
        )
    )
    list(
        session.scalars(
            select(StudyKit)
            .where(StudyKit.id.in_(kit_ids))
            .order_by(StudyKit.id)
            .with_for_update()
        )
    )
    jobs = list(
        session.scalars(
            select(StudyMaterialJob)
            .where(
                StudyMaterialJob.id.in_(job_ids),
                StudyMaterialJob.status.in_(("queued", "processing")),
            )
            .order_by(StudyMaterialJob.id)
            .with_for_update()
        )
    )
    for job in jobs:
        job.status = "cancelled"
        job.cancel_requested_at = current_time
        job.lease_token = None
        job.lease_expires_at = None
        job.next_attempt_at = None
        job.completed_at = current_time
    return {job.id for job in jobs}


def cancel_jobs_for_kit(
    session: Session, study_kit_id: UUID, *, now: datetime | None = None
) -> set[UUID]:
    current_time = now or datetime.now(timezone.utc)
    jobs = list(
        session.scalars(
            select(StudyMaterialJob)
            .where(
                StudyMaterialJob.study_kit_id == study_kit_id,
                StudyMaterialJob.status.in_(("queued", "processing")),
            )
            .order_by(StudyMaterialJob.id)
            .with_for_update()
        )
    )
    for job in jobs:
        job.status = "cancelled"
        job.cancel_requested_at = current_time
        job.lease_token = None
        job.lease_expires_at = None
        job.next_attempt_at = None
        job.completed_at = current_time
    return {job.id for job in jobs}


def finalize_study_material_job(
    session_factory: sessionmaker[Session],
    lease: StudyMaterialLease,
    *,
    title: str,
    content: dict[str, Any],
    citations: Sequence[StudyMaterialCitationInput],
    now: datetime | None = None,
) -> bool:
    if not title.strip() or len(title) > 160:
        raise ValueError("Study material title is invalid")
    if not isinstance(content, dict):
        raise ValueError("Study material content must be an object")

    with session_factory() as session:
        source_rows = list(
            session.scalars(
                select(StudyMaterialJobSource)
                .where(StudyMaterialJobSource.job_id == lease.job_id)
                .order_by(StudyMaterialJobSource.document_id)
            )
        )
        documents = _lock_source_documents(
            session, [source.document_id for source in source_rows]
        )
        heads = _lock_index_heads(session, [document.id for document in documents])
        kit = session.scalar(
            select(StudyKit)
            .where(
                StudyKit.id == lease.study_kit_id,
                StudyKit.user_id == lease.user_id,
                StudyKit.study_space_id == lease.study_space_id,
                StudyKit.archived_at.is_(None),
            )
            .with_for_update()
        )
        job = session.scalar(
            select(StudyMaterialJob)
            .where(
                StudyMaterialJob.id == lease.job_id,
                StudyMaterialJob.user_id == lease.user_id,
                StudyMaterialJob.study_space_id == lease.study_space_id,
                StudyMaterialJob.study_kit_id == lease.study_kit_id,
                StudyMaterialJob.status == "processing",
                StudyMaterialJob.lease_token == lease.token,
                StudyMaterialJob.cancel_requested_at.is_(None),
            )
            .with_for_update()
        )
        current_time = now or datetime.now(timezone.utc)
        if (
            kit is None
            or job is None
            or job.lease_expires_at is None
            or job.lease_expires_at <= current_time
        ):
            session.rollback()
            return False
        if len(documents) != len(source_rows):
            session.rollback()
            return False

        subject = session.scalar(
            select(Subject).where(
                Subject.id == kit.subject_id,
                Subject.user_id == lease.user_id,
                Subject.archived_at.is_(None),
            )
        )
        study_space = session.scalar(
            select(StudySpace).where(
                StudySpace.id == lease.study_space_id,
                StudySpace.subject_id == kit.subject_id,
                StudySpace.archived_at.is_(None),
            )
        )
        if subject is None or study_space is None:
            session.rollback()
            return False

        source_by_document = {source.document_id: source for source in source_rows}
        if len(source_by_document) != len(source_rows):
            session.rollback()
            return False
        current_sources: dict[tuple[UUID, UUID], StudyMaterialJobSource] = {}
        for document in documents:
            source = source_by_document.get(document.id)
            head = heads.get(document.id)
            if (
                source is None
                or document.user_id != lease.user_id
                or document.study_space_id != lease.study_space_id
                or document.status != "completed"
                or not document.has_extractable_text
                or document.content_sha256 != source.source_document_sha256
                or head is None
                or head.generation_id != source.generation_id
                or head.profile_id != get_settings().ai_embedding_profile_id
            ):
                session.rollback()
                return False
            generation = session.scalar(
                select(DocumentIndexGeneration).where(
                    DocumentIndexGeneration.id == source.generation_id,
                    DocumentIndexGeneration.document_id == document.id,
                    DocumentIndexGeneration.status == "ready",
                    DocumentIndexGeneration.embedding_profile_id == head.profile_id,
                    DocumentIndexGeneration.source_document_sha256 == document.content_sha256,
                    DocumentIndexGeneration.chunking_version == CHUNKING_VERSION,
                )
            )
            if generation is None:
                session.rollback()
                return False
            current_sources[(document.id, generation.id)] = source

        for citation in citations:
            if (citation.document_id, citation.generation_id) not in current_sources:
                raise ValueError("Citation is outside the job source set")
            chunk_exists = session.scalar(
                select(DocumentChunk.chunk_index).where(
                    DocumentChunk.document_id == citation.document_id,
                    DocumentChunk.generation_id == citation.generation_id,
                    DocumentChunk.chunk_index == citation.chunk_index,
                )
            )
            if chunk_exists is None:
                raise ValueError("Citation chunk is unavailable")

        current_time = now or datetime.now(timezone.utc)
        if job.lease_expires_at is None or job.lease_expires_at <= current_time:
            session.rollback()
            return False
        next_version = (
            session.scalar(
                select(func.max(StudyMaterial.version_number)).where(
                    StudyMaterial.study_kit_id == kit.id,
                    StudyMaterial.kind == "notes",
                )
            )
            or 0
        ) + 1
        material = StudyMaterial(
            id=uuid4(),
            user_id=lease.user_id,
            study_kit_id=kit.id,
            job_id=job.id,
            kind="notes",
            schema_version=1,
            version_number=next_version,
            title=title,
            content=content,
        )
        session.add(material)
        for citation in citations:
            session.add(
                StudyMaterialCitation(
                    study_material_id=material.id,
                    job_id=job.id,
                    citation_key=citation.citation_key,
                    document_id=citation.document_id,
                    generation_id=citation.generation_id,
                    chunk_index=citation.chunk_index,
                )
            )
        job.status = "completed"
        job.lease_token = None
        job.lease_expires_at = None
        job.cancel_requested_at = None
        job.completed_at = current_time
        job.error_code = None
        job.next_attempt_at = None
        session.commit()
        return True


def delete_document_dependent_work(
    session: Session, document_ids: Sequence[UUID]
) -> set[UUID]:
    documents = _lock_source_documents(session, document_ids)
    ordered_ids = [document.id for document in documents]
    if not ordered_ids:
        return set()
    _lock_index_heads(session, ordered_ids)
    _lock_document_index_generations_and_jobs(session, ordered_ids)

    job_ids = list(
        session.scalars(
            select(StudyMaterialJob.id)
            .join(
                StudyMaterialJobSource,
                StudyMaterialJobSource.job_id == StudyMaterialJob.id,
            )
            .where(StudyMaterialJobSource.document_id.in_(ordered_ids))
            .distinct()
            .order_by(StudyMaterialJob.id)
        )
    )
    if not job_ids:
        return set()

    kit_ids = list(
        session.scalars(
            select(StudyMaterialJob.study_kit_id)
            .where(StudyMaterialJob.id.in_(job_ids))
            .distinct()
            .order_by(StudyMaterialJob.study_kit_id)
        )
    )
    list(
        session.scalars(
            select(StudyKit)
            .where(StudyKit.id.in_(kit_ids))
            .order_by(StudyKit.id)
            .with_for_update()
        )
    )
    jobs = list(
        session.scalars(
            select(StudyMaterialJob)
            .where(StudyMaterialJob.id.in_(job_ids))
            .order_by(StudyMaterialJob.id)
            .with_for_update()
        )
    )
    locked_job_ids = {job.id for job in jobs}
    session.execute(
        delete(StudyMaterialCitation).where(
            StudyMaterialCitation.job_id.in_(locked_job_ids)
        )
    )
    session.execute(
        delete(StudyMaterial).where(StudyMaterial.job_id.in_(locked_job_ids))
    )
    session.execute(
        delete(StudyMaterialJobSource).where(
            StudyMaterialJobSource.job_id.in_(locked_job_ids)
        )
    )
    session.execute(
        delete(StudyMaterialJob).where(StudyMaterialJob.id.in_(locked_job_ids))
    )
    return locked_job_ids


def delete_study_kit_jobs(session: Session, study_kit_id: UUID) -> set[UUID]:
    job_ids = list(
        session.scalars(
            select(StudyMaterialJob.id)
            .where(StudyMaterialJob.study_kit_id == study_kit_id)
            .order_by(StudyMaterialJob.id)
        )
    )
    if not job_ids:
        return set()
    jobs = list(
        session.scalars(
            select(StudyMaterialJob)
            .where(StudyMaterialJob.id.in_(job_ids))
            .order_by(StudyMaterialJob.id)
            .with_for_update()
        )
    )
    locked_job_ids = {job.id for job in jobs}
    session.execute(
        delete(StudyMaterialCitation).where(
            StudyMaterialCitation.job_id.in_(locked_job_ids)
        )
    )
    session.execute(
        delete(StudyMaterial).where(StudyMaterial.job_id.in_(locked_job_ids))
    )
    session.execute(
        delete(StudyMaterialJobSource).where(
            StudyMaterialJobSource.job_id.in_(locked_job_ids)
        )
    )
    session.execute(
        delete(StudyMaterialJob).where(StudyMaterialJob.id.in_(locked_job_ids))
    )
    return locked_job_ids
