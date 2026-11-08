from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models import (
    AIEmbeddingProfile,
    Base,
    Document,
    DocumentIndexGeneration,
    DocumentIndexJob,
    StudySpace,
    Subject,
    User,
)
from app.services import document_indexing

PROFILE_ID = "openai-text-embedding-3-small-1536-v1"
CHUNKING_VERSION = "page-aware-utf8-byte-overlap-v1"


@pytest.fixture
def session_factory() -> sessionmaker[Session]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    with factory.begin() as session:
        session.add(
            AIEmbeddingProfile(
                singleton_key=True,
                profile_id=PROFILE_ID,
                provider="openai",
                model_name="text-embedding-3-small",
                dimensions=1536,
                distance_metric="cosine",
            )
        )
    yield factory
    engine.dispose()


def _settings(profile_id: str = PROFILE_ID) -> Settings:
    return Settings(
        _env_file=None,
        ai_enabled=True,
        openai_api_key="test-key-not-valid",
        ai_pricing_version="test-pricing-v1",
        ai_embedding_input_usd_per_million=Decimal("0.02"),
        ai_structured_input_usd_per_million=Decimal("0.15"),
        ai_structured_output_usd_per_million=Decimal("0.60"),
        ai_embedding_profile_id=profile_id,
    )


def _create_document(
    session: Session,
    *,
    suffix: str,
    processed_at: datetime,
) -> Document:
    user = User(
        email=f"{suffix}-{uuid4().hex}@example.test",
        display_name="Reconciliation test",
        password_hash="test-only",
        status="active",
    )
    session.add(user)
    session.flush()
    subject = Subject(user_id=user.id, name=f"Subject {suffix}")
    session.add(subject)
    session.flush()
    study_space = StudySpace(subject_id=subject.id, name=f"Space {suffix}")
    session.add(study_space)
    session.flush()
    document = Document(
        user_id=user.id,
        subject_id=subject.id,
        study_space_id=study_space.id,
        original_filename=f"{suffix}.pdf",
        mime_type="application/pdf",
        storage_key=f"{uuid4().hex}.pdf",
        content_sha256="a" * 64,
        file_size_bytes=100,
        status="completed",
        has_extractable_text=True,
        processed_at=processed_at,
    )
    session.add(document)
    session.flush()
    return document


def _create_generation(
    session: Session,
    document: Document,
    *,
    status: str,
    profile_id: str = PROFILE_ID,
    chunking_version: str = CHUNKING_VERSION,
) -> DocumentIndexGeneration:
    generation = DocumentIndexGeneration(
        document_id=document.id,
        generation_number=1,
        source_document_sha256=document.content_sha256,
        embedding_profile_id=profile_id,
        model_name="text-embedding-3-small",
        dimensions=1536,
        distance_metric="cosine",
        chunking_version=chunking_version,
        token_count_strategy="utf8-byte-upper-bound-v1",
        status=status,
    )
    session.add(generation)
    session.flush()
    return generation


def test_reconciliation_excludes_current_ready_generation_and_missing_job(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    with session_factory.begin() as session:
        document = _create_document(
            session, suffix="ready", processed_at=datetime.now(timezone.utc)
        )
        generation = _create_generation(session, document, status="ready")
        document_id = document.id
        generation_id = generation.id

    assert document_indexing.reconcile_missing_indexes(session_factory, limit=1) == 0
    with session_factory() as session:
        assert session.get(DocumentIndexGeneration, generation_id) is not None
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexJob).where(
                DocumentIndexJob.document_id == document_id
            )
        ) == 0


def test_reconciliation_does_not_starve_older_missing_document(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    now = datetime.now(timezone.utc)
    with session_factory.begin() as session:
        for index in range(3):
            indexed = _create_document(
                session,
                suffix=f"indexed-{index}",
                processed_at=now - timedelta(days=3 - index),
            )
            _create_generation(session, indexed, status="ready")
        missing = _create_document(
            session, suffix="older-missing", processed_at=now - timedelta(days=1)
        )
        missing_id = missing.id

    assert document_indexing.reconcile_missing_indexes(session_factory, limit=1) == 1
    with session_factory() as session:
        generations = list(
            session.scalars(
                select(DocumentIndexGeneration).where(
                    DocumentIndexGeneration.document_id == missing_id
                )
            )
        )
        assert len(generations) == 1
        assert generations[0].status == "building"
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexJob).where(
                DocumentIndexJob.document_id == missing_id,
                DocumentIndexJob.status == "queued",
            )
        ) == 1


def test_reconciliation_recovers_failed_generation_job(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    with session_factory.begin() as session:
        document = _create_document(
            session, suffix="failed", processed_at=datetime.now(timezone.utc)
        )
        generation = _create_generation(session, document, status="failed")
        generation.error_code = "indexing_failed"
        job = DocumentIndexJob(
            document_id=document.id,
            generation_id=generation.id,
            status="failed",
            attempts_used=5,
            error_code="indexing_failed",
        )
        session.add(job)
        session.flush()
        document_id = document.id
        generation_id = generation.id
        job_id = job.id

    assert document_indexing.reconcile_missing_indexes(session_factory, limit=1) == 0
    with session_factory() as session:
        generation = session.get(DocumentIndexGeneration, generation_id)
        job = session.get(DocumentIndexJob, job_id)
        assert generation is not None and generation.status == "building"
        assert job is not None and job.status == "queued"
        assert job.attempts_used == 0
        assert job.error_code is None
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexGeneration).where(
                DocumentIndexGeneration.document_id == document_id
            )
        ) == 1


def test_reconciliation_creates_missing_job_without_duplicate_generation(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    with session_factory.begin() as session:
        document = _create_document(
            session, suffix="missing-job", processed_at=datetime.now(timezone.utc)
        )
        _create_generation(session, document, status="building")
        document_id = document.id

    assert document_indexing.reconcile_missing_indexes(session_factory, limit=1) == 0
    with session_factory() as session:
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexGeneration).where(
                DocumentIndexGeneration.document_id == document_id
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexJob).where(
                DocumentIndexJob.document_id == document_id,
                DocumentIndexJob.status == "queued",
            )
        ) == 1


def test_expired_processing_lease_is_reclaimed(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    with session_factory.begin() as session:
        document = _create_document(
            session, suffix="expired-lease", processed_at=datetime.now(timezone.utc)
        )
        generation = _create_generation(session, document, status="building")
        job = DocumentIndexJob(
            document_id=document.id,
            generation_id=generation.id,
            status="processing",
            attempts_used=1,
            lease_token=uuid4(),
            lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1),
        )
        session.add(job)
        session.flush()
        job_id = job.id

    lease = document_indexing._claim_index_job(session_factory)

    assert lease is not None
    with session_factory() as session:
        job = session.get(DocumentIndexJob, job_id)
        assert job is not None and job.status == "processing"
        assert job.attempts_used == 2
        assert job.lease_token == lease.token
        assert job.lease_expires_at is not None
        assert job.lease_expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)


def test_profile_change_is_rejected_without_database_profile_rotation(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The singleton profile row cannot rotate while generations reference it."""
    settings = _settings("openai-text-embedding-3-small-1536-v2")
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)

    with pytest.raises(
        RuntimeError,
        match="AI embedding profile configuration does not match the database",
    ):
        document_indexing.reconcile_missing_indexes(session_factory, limit=1)


def test_chunking_version_change_creates_current_generation(
    session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    monkeypatch.setattr(document_indexing, "CHUNKING_VERSION", "page-aware-v2")
    with session_factory.begin() as session:
        document = _create_document(
            session, suffix="version-chunking", processed_at=datetime.now(timezone.utc)
        )
        old_generation = _create_generation(session, document, status="ready")
        old_generation_id = old_generation.id
        document_id = document.id

    assert document_indexing.reconcile_missing_indexes(session_factory, limit=1) == 1
    with session_factory() as session:
        generations = list(
            session.scalars(
                select(DocumentIndexGeneration)
                .where(DocumentIndexGeneration.document_id == document_id)
                .order_by(DocumentIndexGeneration.generation_number)
            )
        )
        assert len(generations) == 2
        assert generations[0].id == old_generation_id
        assert generations[0].status == "ready"
        assert generations[1].status == "building"
        assert generations[0].embedding_profile_id == PROFILE_ID
        assert generations[1].embedding_profile_id == PROFILE_ID
        assert generations[1].chunking_version == "page-aware-v2"
