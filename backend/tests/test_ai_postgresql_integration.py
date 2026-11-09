import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import and_, create_engine, func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings
from app.models import (
    AIEmbeddingProfile,
    AIUsageEvent,
    AIUsageReservation,
    Document,
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentIndexGeneration,
    DocumentIndexHead,
    DocumentIndexJob,
    DocumentPage,
    StudySpace,
    Subject,
    User,
)
from app.services import document_indexing, document_worker
from app.services.document_indexing import (
    IndexLease,
    _claim_index_job,
    _activate_generation,
    process_next_index_job,
)
from app.services.ai_retrieval import retrieve_passages
from app.services.ai_usage_reservations import (
    UsageLimitExceeded,
    mark_dispatching,
    recover_expired_reservations,
    reserve_usage,
    settle_usage,
)
from app.services.document_worker import DocumentLease, _publish_result
from app.ai.provider import EmbeddingResult
from postgresql_test_safety import validate_ai_test_database_url

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROFILE_ID = "openai-text-embedding-3-small-1536-v1"
pytestmark = pytest.mark.integration
logger = logging.getLogger(__name__)


@pytest.fixture
def ai_test_factory(monkeypatch: pytest.MonkeyPatch) -> sessionmaker[Session]:
    try:
        validated_url = validate_ai_test_database_url(
            os.getenv("AI_TEST_DATABASE_URL"),
            os.getenv("DATABASE_URL"),
        )
    except ValueError as error:
        pytest.fail(str(error))
    validated_url_string = validated_url.render_as_string(hide_password=False)

    monkeypatch.setenv("DATABASE_URL", validated_url_string)
    monkeypatch.setenv("ENVIRONMENT", "test")
    original_env_file = Settings.model_config.get("env_file")
    Settings.model_config["env_file"] = None
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", validated_url_string.replace("%", "%%"))
    try:
        command.upgrade(config, "head")
    finally:
        Settings.model_config["env_file"] = original_env_file

    engine = create_engine(validated_url, pool_pre_ping=True)
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT current_setting('server_version_num')::int")) >= 180000
        assert connection.scalar(
            text("SELECT EXISTS (SELECT 1 FROM pg_extension WHERE extname='vector')")
        )
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def _settings(**overrides) -> Settings:
    values = {
        "_env_file": None,
        "ai_enabled": True,
        "openai_api_key": "test-key-not-valid",
        "ai_pricing_version": "test-pricing-v1",
        "ai_embedding_input_usd_per_million": Decimal("0.02"),
        "ai_structured_input_usd_per_million": Decimal("0.15"),
        "ai_structured_output_usd_per_million": Decimal("0.60"),
    }
    values.update(overrides)
    return Settings(**values)


def _create_document(
    session: Session,
    *,
    user: User,
    suffix: str,
    status: str = "completed",
) -> tuple[Document, Subject, StudySpace]:
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
        content_sha256=("a" if suffix.endswith("a") else "b") * 64,
        file_size_bytes=100,
        status=status,
        has_extractable_text=status == "completed",
        attempts_used=1 if status == "processing" else 0,
    )
    if status == "processing":
        document.lease_token = uuid4()
        document.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
    session.add(document)
    session.flush()
    return document, subject, study_space


def _create_user(session: Session, suffix: str) -> User:
    user = User(
        email=f"ai-{suffix}-{uuid4().hex}@example.test",
        display_name="AI integration learner",
        password_hash="integration-only",
        status="active",
    )
    session.add(user)
    session.flush()
    return user


def _cleanup_test_user(
    session_factory: sessionmaker[Session],
    user_id: UUID | None,
    *,
    test_failed: bool,
    description: str,
) -> None:
    if user_id is None:
        return
    try:
        with session_factory() as session:
            user = session.get(User, user_id)
            if user is not None:
                session.delete(user)
                session.commit()
    except Exception:
        if test_failed:
            logger.exception("Failed to clean up %s integration test data", description)
        else:
            raise


def _create_ready_index(session: Session, document: Document) -> DocumentIndexGeneration:
    profile = session.get(AIEmbeddingProfile, True)
    assert profile is not None
    session.add(DocumentPage(document_id=document.id, page_number=1, extracted_text="private study passage"))
    generation = DocumentIndexGeneration(
        document_id=document.id,
        generation_number=1,
        source_document_sha256=document.content_sha256,
        embedding_profile_id=PROFILE_ID,
        model_name="text-embedding-3-small",
        dimensions=1536,
        distance_metric="cosine",
        chunking_version="page-aware-utf8-byte-overlap-v1",
        token_count_strategy="utf8-byte-upper-bound-v1",
        status="ready",
        ready_at=datetime.now(timezone.utc),
    )
    session.add(generation)
    session.flush()
    chunk = DocumentChunk(
        document_id=document.id,
        generation_id=generation.id,
        chunk_index=0,
        page_start=1,
        page_end=1,
        text="private study passage",
        utf8_byte_upper_bound=len(b"private study passage"),
        content_sha256="c" * 64,
    )
    session.add(chunk)
    session.flush()
    session.add(
        DocumentChunkEmbedding(
            document_id=document.id,
            generation_id=generation.id,
            chunk_index=0,
            profile_id=profile.profile_id,
            embedding=[1.0] + [0.0] * 1535,
        )
    )
    session.add(
        DocumentIndexHead(
            document_id=document.id,
            profile_id=profile.profile_id,
            generation_id=generation.id,
        )
    )
    session.flush()
    return generation


def test_extension_vector_type_and_composite_isolation(ai_test_factory) -> None:
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "fk")
            first, _, _ = _create_document(session, user=user, suffix="firsta")
            second, _, _ = _create_document(session, user=user, suffix="secondb")
            generation = _create_ready_index(session, first)
            first_document_id = first.id
            job = DocumentIndexJob(
                document_id=second.id,
                generation_id=generation.id,
                status="queued",
            )
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(job)
                    session.flush()

    with ai_test_factory() as session:
        vector_type = session.scalar(
            text(
                "SELECT format_type(a.atttypid, a.atttypmod) "
                "FROM pg_attribute a "
                "WHERE a.attrelid='document_chunk_embeddings'::regclass "
                "AND a.attname='embedding'"
            )
        )
        assert vector_type == "vector(1536)"
        assert session.scalar(
            select(func.count()).select_from(AIEmbeddingProfile).where(
                AIEmbeddingProfile.profile_id == PROFILE_ID
            )
        ) == 1
        hnsw_index = session.scalar(
            text(
                "SELECT indexdef FROM pg_indexes "
                "WHERE indexname='ix_document_chunk_embeddings_hnsw'"
            )
        )
        assert hnsw_index is not None and "USING hnsw" in hnsw_index

    with ai_test_factory() as session:
        document = session.get(Document, first_document_id)
        assert document is not None
        session.delete(document)
        session.commit()
        assert session.get(DocumentIndexHead, first_document_id) is None
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexGeneration).where(
                DocumentIndexGeneration.document_id == first_document_id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(DocumentChunkEmbedding).where(
                DocumentChunkEmbedding.document_id == first_document_id
            )
        ) == 0


def test_retrieval_is_owner_and_archive_scoped(ai_test_factory) -> None:
    class FakeProvider:
        def __init__(self, _settings: Settings) -> None:
            pass

        def embed(self, _texts: list[str]) -> EmbeddingResult:
            return EmbeddingResult(
                vectors=(tuple([1.0] + [0.0] * 1535),),
                input_tokens=8,
                provider_request_id="fake-request",
            )

    settings = _settings()
    with ai_test_factory() as session:
        with session.begin():
            owner = _create_user(session, "retrieval-owner")
            other = _create_user(session, "retrieval-other")
            owned_doc, owned_subject, _ = _create_document(
                session, user=owner, suffix="owned"
            )
            foreign_doc, _, _ = _create_document(session, user=other, suffix="foreign")
            _create_ready_index(session, owned_doc)
            _create_ready_index(session, foreign_doc)
            owned_id = owned_doc.id
            foreign_id = foreign_doc.id
            owner_id = owner.id
            subject_id = owned_subject.id

    with ai_test_factory() as session:
        hits = retrieve_passages(
            session,
            user_id=owner_id,
            query="test query",
            settings=settings,
            provider_factory=FakeProvider,
        )
        assert [hit.document_id for hit in hits] == [owned_id]
        assert hits[0].page_start == hits[0].page_end == 1
        assert hits[0].text == "private study passage"

        foreign_filter = retrieve_passages(
            session,
            user_id=owner_id,
            query="test query",
            document_ids=[foreign_id],
            settings=settings,
            provider_factory=FakeProvider,
        )
        assert foreign_filter == []
        session.execute(
            text("UPDATE subjects SET archived_at=CURRENT_TIMESTAMP WHERE id=:id"),
            {"id": subject_id},
        )
        session.commit()
        archived_hits = retrieve_passages(
            session,
            user_id=owner_id,
            query="test query",
            settings=settings,
            provider_factory=FakeProvider,
        )
        assert archived_hits == []


def test_extraction_handoff_is_atomic_and_skips_no_text(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_indexing, "get_settings", lambda: _settings())
    monkeypatch.setattr(document_worker, "get_settings", lambda: _settings())
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "handoff")
            document, _, _ = _create_document(
                session, user=user, suffix="handoff", status="processing"
            )
            lease = DocumentLease(
                document_id=document.id,
                user_id=user.id,
                subject_id=document.subject_id,
                study_space_id=document.study_space_id,
                storage_key=document.storage_key,
                token=document.lease_token,
                attempts_used=1,
            )
            document_id = document.id

    _publish_result(ai_test_factory, lease, ["extracted text"], has_text=True)
    with ai_test_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None and document.status == "completed"
        assert document.has_extractable_text is True
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexGeneration).where(
                DocumentIndexGeneration.document_id == document_id
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexJob).where(
                DocumentIndexJob.document_id == document_id
            )
        ) == 1

    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "empty")
            document, _, _ = _create_document(
                session, user=user, suffix="empty", status="processing"
            )
            lease = DocumentLease(
                document_id=document.id,
                user_id=user.id,
                subject_id=document.subject_id,
                study_space_id=document.study_space_id,
                storage_key=document.storage_key,
                token=document.lease_token,
                attempts_used=1,
            )
            document_id = document.id
    _publish_result(ai_test_factory, lease, [""], has_text=False)
    with ai_test_factory() as session:
        assert session.get(Document, document_id).status == "completed"
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexJob).where(
                DocumentIndexJob.document_id == document_id
            )
        ) == 0


def test_extraction_handoff_rolls_back_on_index_enqueue_failure(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "handoff-rollback")
            document, _, _ = _create_document(
                session, user=user, suffix="handoff-rollback", status="processing"
            )
            lease = DocumentLease(
                document_id=document.id,
                user_id=user.id,
                subject_id=document.subject_id,
                study_space_id=document.study_space_id,
                storage_key=document.storage_key,
                token=document.lease_token,
                attempts_used=1,
            )
            document_id = document.id

    def fail_enqueue(*_args, **_kwargs):
        raise RuntimeError("injected generation enqueue failure")

    monkeypatch.setattr(document_indexing, "get_settings", lambda: _settings())
    monkeypatch.setattr(document_worker, "get_settings", lambda: _settings())
    monkeypatch.setattr(document_indexing, "ensure_index_generation", fail_enqueue)
    monkeypatch.setattr(document_worker, "ensure_index_generation", fail_enqueue)
    with pytest.raises(RuntimeError, match="injected"):
        _publish_result(ai_test_factory, lease, ["extracted text"], has_text=True)
    with ai_test_factory() as session:
        document = session.get(Document, document_id)
        assert document is not None and document.status == "processing"
        assert document.lease_token == lease.token
        assert session.scalar(
            select(func.count()).select_from(DocumentPage).where(
                DocumentPage.document_id == document_id
            )
        ) == 0
        assert session.scalar(
            select(func.count()).select_from(DocumentIndexJob).where(
                DocumentIndexJob.document_id == document_id
            )
        ) == 0


def test_legacy_reconciliation_is_concurrent_and_idempotent(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "reconcile")
            document, _, _ = _create_document(session, user=user, suffix="reconcile")
            session.add(
                DocumentPage(
                    document_id=document.id,
                    page_number=1,
                    extracted_text="legacy extracted text",
                )
            )
            document_id = document.id
            user_id = user.id

    def reconcile() -> int:
        return document_indexing.reconcile_missing_indexes(ai_test_factory, limit=1)

    try:
        with ai_test_factory() as isolation_session:
            isolation_session.execute(
                text("LOCK TABLE documents, subjects, study_spaces IN SHARE MODE")
            )
            list(
                isolation_session.scalars(
                    select(Document.id)
                    .join(Subject, Subject.id == Document.subject_id)
                    .join(StudySpace, StudySpace.id == Document.study_space_id)
                    .where(
                        Document.id != document_id,
                        Document.status == "completed",
                        Document.has_extractable_text.is_(True),
                        Subject.archived_at.is_(None),
                        StudySpace.archived_at.is_(None),
                    )
                    .with_for_update(of=Document)
                )
            )
            with ThreadPoolExecutor(max_workers=2) as executor:
                enqueue_counts = list(executor.map(lambda _: reconcile(), range(2)))
            assert len(enqueue_counts) == 2
            assert sum(enqueue_counts) == 1
            with ai_test_factory() as session:
                assert session.scalar(
                    select(func.count()).select_from(DocumentIndexGeneration).where(
                        DocumentIndexGeneration.document_id == document_id
                    )
                ) == 1
                assert session.scalar(
                    select(func.count()).select_from(DocumentIndexJob).where(
                        DocumentIndexJob.document_id == document_id
                    )
                ) == 1
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="legacy reconciliation",
        )


def test_index_worker_persists_embeddings_and_activates_generation(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    user_id = None
    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "worker")
                older_document, _, _ = _create_document(
                    session, user=user, suffix="worker-older"
                )
                session.add(
                    DocumentPage(
                        document_id=older_document.id,
                        page_number=1,
                        extracted_text="Older queued job.",
                    )
                )
                older_generation = document_indexing.ensure_index_generation(
                    session, older_document, settings=settings
                )
                older_job = session.scalar(
                    select(DocumentIndexJob).where(
                        DocumentIndexJob.generation_id == older_generation.id
                    )
                )
                assert older_job is not None
                older_job_id = older_job.id

                document, _, _ = _create_document(session, user=user, suffix="worker")
                session.add(
                    DocumentPage(
                        document_id=document.id,
                        page_number=1,
                        extracted_text="A concise page for indexing.",
                    )
                )
                generation = document_indexing.ensure_index_generation(
                    session, document, settings=settings
                )
                job = session.scalar(
                    select(DocumentIndexJob).where(
                        DocumentIndexJob.generation_id == generation.id
                    )
                )
                assert job is not None
                lease_token = uuid4()
                job.status = "processing"
                job.attempts_used = 1
                job.lease_token = lease_token
                job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=5)
                lease = IndexLease(
                    job_id=job.id,
                    document_id=document.id,
                    generation_id=generation.id,
                    user_id=user.id,
                    token=lease_token,
                    attempts_used=job.attempts_used,
                )
                document_id = document.id
                generation_id = generation.id
                job_id = job.id
                user_id = user.id

        monkeypatch.setattr(
            document_indexing,
            "_claim_index_job",
            lambda _session_factory: lease,
        )

        class FakeProvider:
            def __init__(self, _settings: Settings) -> None:
                pass

            def embed(self, texts: list[str]) -> EmbeddingResult:
                vector = tuple([1.0] + [0.0] * 1535)
                return EmbeddingResult(
                    vectors=tuple(vector for _ in texts),
                    input_tokens=sum(len(text.encode("utf-8")) for text in texts),
                    provider_request_id="fake-worker-request",
                )

        assert process_next_index_job(ai_test_factory, provider_factory=FakeProvider)
        with ai_test_factory() as session:
            generation = session.get(DocumentIndexGeneration, generation_id)
            head = session.get(DocumentIndexHead, document_id)
            job = session.get(DocumentIndexJob, job_id)
            older_job = session.get(DocumentIndexJob, older_job_id)
            diagnostics = (
                f"job_id={job_id}, status={job.status if job else None}, "
                f"error_code={job.error_code if job else None}, "
                f"generation_status={generation.status if generation else None}, "
                f"worker_logs={caplog.text}"
            )
            assert generation is not None and generation.status == "ready", diagnostics
            assert head is not None and head.generation_id == generation_id
            assert job is not None and job.status == "completed", diagnostics
            assert job.error_code is None, diagnostics
            assert older_job is not None and older_job.status == "queued"
            assert older_job.error_code is None
            assert session.scalar(
                select(func.count()).select_from(DocumentChunkEmbedding).where(
                    DocumentChunkEmbedding.generation_id == generation_id
                )
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(AIUsageEvent).where(
                    AIUsageEvent.document_id == document_id
                )
            ) == 1
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="worker activation",
        )


def test_index_job_claim_prioritizes_older_queued_job(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "worker-order")
            user_id = user.id
            older_document, _, _ = _create_document(
                session, user=user, suffix="worker-order-older"
            )
            session.add(
                DocumentPage(
                    document_id=older_document.id,
                    page_number=1,
                    extracted_text="Older queued job.",
                )
            )
            older_generation = document_indexing.ensure_index_generation(
                session, older_document, settings=settings
            )
            older_job = session.scalar(
                select(DocumentIndexJob).where(
                    DocumentIndexJob.generation_id == older_generation.id
                )
            )
            assert older_job is not None
            older_job_id = older_job.id

            newer_document, _, _ = _create_document(
                session, user=user, suffix="worker-order-newer"
            )
            session.add(
                DocumentPage(
                    document_id=newer_document.id,
                    page_number=1,
                    extracted_text="Newer queued job.",
                )
            )
            newer_generation = document_indexing.ensure_index_generation(
                session, newer_document, settings=settings
            )
            newer_job = session.scalar(
                select(DocumentIndexJob).where(
                    DocumentIndexJob.generation_id == newer_generation.id
                )
            )
            assert newer_job is not None
            newer_job_id = newer_job.id

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
            earliest_eligible_created_at = session.scalar(
                select(func.min(DocumentIndexJob.created_at))
                .join(Document, Document.id == DocumentIndexJob.document_id)
                .join(Subject, Subject.id == Document.subject_id)
                .join(StudySpace, StudySpace.id == Document.study_space_id)
                .where(
                    eligible,
                    Subject.archived_at.is_(None),
                    StudySpace.archived_at.is_(None),
                )
            )
            assert earliest_eligible_created_at is not None
            older_job.created_at = earliest_eligible_created_at - timedelta(seconds=2)
            newer_job.created_at = earliest_eligible_created_at - timedelta(seconds=1)

    try:
        lease = _claim_index_job(ai_test_factory)

        assert lease is not None
        assert lease.job_id == older_job_id
        with ai_test_factory() as session:
            older_job = session.get(DocumentIndexJob, older_job_id)
            newer_job = session.get(DocumentIndexJob, newer_job_id)
            assert older_job is not None and older_job.status == "processing"
            assert newer_job is not None and newer_job.status == "queued"
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="job claim",
        )


def test_generation_activation_keeps_old_index_until_new_is_complete(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(document_indexing, "get_settings", lambda: _settings())
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "activation")
            document, _, _ = _create_document(session, user=user, suffix="activation")
            old_generation = _create_ready_index(session, document)
            session.add(
                DocumentIndexJob(
                    document_id=document.id,
                    generation_id=old_generation.id,
                    status="completed",
                    attempts_used=1,
                )
            )
            old_generation_id = old_generation.id
            document.content_sha256 = "d" * 64
            new_generation = DocumentIndexGeneration(
                document_id=document.id,
                generation_number=2,
                source_document_sha256=document.content_sha256,
                embedding_profile_id=PROFILE_ID,
                model_name="text-embedding-3-small",
                dimensions=1536,
                distance_metric="cosine",
                chunking_version="page-aware-utf8-byte-overlap-v1",
                token_count_strategy="utf8-byte-upper-bound-v1",
                status="building",
            )
            session.add(new_generation)
            session.flush()
            session.add(
                DocumentChunk(
                    document_id=document.id,
                    generation_id=new_generation.id,
                    chunk_index=0,
                    page_start=1,
                    page_end=1,
                    text="replacement study passage",
                    utf8_byte_upper_bound=len(b"replacement study passage"),
                    content_sha256="e" * 64,
                )
            )
            session.add(
                DocumentChunkEmbedding(
                    document_id=document.id,
                    generation_id=new_generation.id,
                    chunk_index=0,
                    profile_id=PROFILE_ID,
                    embedding=[0.0, 1.0] + [0.0] * 1534,
                )
            )
            job = DocumentIndexJob(
                document_id=document.id,
                generation_id=new_generation.id,
                status="processing",
                attempts_used=1,
                lease_token=uuid4(),
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            session.add(job)
            session.flush()
            lease = IndexLease(
                job_id=job.id,
                document_id=document.id,
                generation_id=new_generation.id,
                user_id=user.id,
                token=job.lease_token,
                attempts_used=1,
            )
            document_id = document.id
            new_generation_id = new_generation.id

    stale_lease = IndexLease(
        job_id=lease.job_id,
        document_id=lease.document_id,
        generation_id=lease.generation_id,
        user_id=lease.user_id,
        token=uuid4(),
        attempts_used=lease.attempts_used,
    )
    assert not _activate_generation(ai_test_factory, stale_lease, expected_count=1)
    with pytest.raises(RuntimeError, match="generation is incomplete"):
        _activate_generation(ai_test_factory, lease, expected_count=2)
    with ai_test_factory() as session:
        head = session.get(DocumentIndexHead, document_id)
        assert head is not None and head.generation_id == old_generation_id
        assert session.get(DocumentIndexGeneration, old_generation_id).status == "ready"
        assert session.get(DocumentIndexGeneration, new_generation_id).status == "building"

    assert _activate_generation(ai_test_factory, lease, expected_count=1)
    with ai_test_factory() as session:
        head = session.get(DocumentIndexHead, document_id)
        assert head is not None and head.generation_id == new_generation_id
        assert session.get(DocumentIndexGeneration, old_generation_id).status == "ready"
        assert session.get(DocumentIndexGeneration, new_generation_id).status == "ready"
        assert session.get(DocumentChunkEmbedding, (old_generation_id, 0)) is not None


def test_usage_reservations_serialize_concurrent_quota(ai_test_factory) -> None:
    settings = _settings(ai_daily_input_token_limit=100)
    with ai_test_factory() as session:
        with session.begin():
            user_id = _create_user(session, "quota").id

    def attempt(index: int) -> bool:
        with ai_test_factory() as session:
            try:
                reserve_usage(
                    session,
                    user_id=user_id,
                    operation="query_embedding",
                    idempotency_key=f"quota:{uuid4()}:{index}",
                    input_token_upper_bound=10,
                    settings=settings,
                )
                return True
            except UsageLimitExceeded:
                return False

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(attempt, range(24)))
    assert sum(results) == 10
    with ai_test_factory() as session:
        reservations = list(
            session.scalars(
                select(AIUsageReservation).where(
                    AIUsageReservation.user_id == user_id,
                    AIUsageReservation.status == "reserved",
                )
            )
        )
        assert sum(item.reserved_input_tokens for item in reservations) == 100


def test_reservation_dispatch_crash_boundaries_and_settlement(ai_test_factory) -> None:
    settings = _settings()
    user_id = None
    test_reservation_ids: set[UUID] = set()

    def recover_test_reservations() -> int:
        with ai_test_factory() as isolation_session:
            # The recovery sweep uses SKIP LOCKED, so lock every other sweepable row.
            list(
                isolation_session.scalars(
                    select(AIUsageReservation.id)
                    .where(
                        AIUsageReservation.status.in_(
                            ("reserved", "dispatching", "uncertain")
                        ),
                        AIUsageReservation.id.not_in(test_reservation_ids),
                    )
                    .with_for_update()
                )
            )
            return recover_expired_reservations(ai_test_factory)

    try:
        with ai_test_factory() as session:
            with session.begin():
                user_id = _create_user(session, "crash").id
        with ai_test_factory() as session:
            undispatched = reserve_usage(
                session,
                user_id=user_id,
                operation="query_embedding",
                idempotency_key=f"before:{uuid4()}",
                input_token_upper_bound=10,
                settings=settings,
            )
            reservation_id = undispatched.id
            test_reservation_ids.add(reservation_id)
            assert not undispatched.dispatch_started_at
        with ai_test_factory() as session:
            reservation = session.get(AIUsageReservation, reservation_id)
            reservation.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        assert recover_test_reservations() == 1
        with ai_test_factory() as session:
            assert session.get(AIUsageReservation, reservation_id).status == "released"

        with ai_test_factory() as session:
            dispatched = reserve_usage(
                session,
                user_id=user_id,
                operation="query_embedding",
                idempotency_key=f"after:{uuid4()}",
                input_token_upper_bound=10,
                settings=settings,
            )
            dispatched_id = dispatched.id
            test_reservation_ids.add(dispatched_id)
            mark_dispatching(session, dispatched_id)
            dispatched.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        assert recover_test_reservations() == 1
        with ai_test_factory() as session:
            uncertain = session.get(AIUsageReservation, dispatched_id)
            assert uncertain.status == "uncertain"
            assert uncertain.expires_at > datetime.now(timezone.utc)
            uncertain.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            session.commit()
        assert recover_test_reservations() == 1
        with ai_test_factory() as session:
            settled = session.get(AIUsageReservation, dispatched_id)
            event = session.scalar(
                select(AIUsageEvent).where(AIUsageEvent.reservation_id == dispatched_id)
            )
            assert settled.status == "estimated"
            assert event is not None and event.accounting_basis == "conservative_estimate"
            assert event.input_tokens == settled.reserved_input_tokens

        with ai_test_factory() as session:
            actual = reserve_usage(
                session,
                user_id=user_id,
                operation="query_embedding",
                idempotency_key=f"actual:{uuid4()}",
                input_token_upper_bound=20,
                settings=settings,
            )
            mark_dispatching(session, actual.id)
            first = settle_usage(session, actual.id, input_tokens=9, settings=settings)
            second = settle_usage(session, actual.id, input_tokens=9, settings=settings)
            assert first.id == second.id
            assert second.accounting_basis == "provider_reported"
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="reservation recovery",
        )
