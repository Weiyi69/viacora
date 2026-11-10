import logging
import os
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import importlib.util
from pathlib import Path
from threading import Barrier, Event, Lock, current_thread, local
import time
from uuid import UUID, uuid4

from fastapi import HTTPException
import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import and_, create_engine, delete, event, func, or_, select, text
from sqlalchemy.exc import IntegrityError, OperationalError
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
    FlashcardReviewEvent,
    StudyKit,
    StudyMaterial,
    StudyMaterialCard,
    StudyMaterialCardCitation,
    StudyMaterialCitation,
    StudyMaterialJob,
    StudyMaterialJobSource,
    StudySpace,
    Subject,
    User,
)
from app.services.flashcard_learning import (
    ReviewIdempotencyConflict,
    create_review_event,
    get_flashcard_progress,
)
from app.services import document_indexing, document_worker
from app.services import study_material_jobs
from app.services.document_indexing import (
    IndexLease,
    _claim_index_job,
    _activate_generation,
    _record_index_failure,
    process_next_index_job,
)
from app.services.study_material_jobs import (
    IdempotencyConflict,
    StudyMaterialCitationInput,
    StudyMaterialCardInput,
    StudyMaterialLease,
    StudyMaterialSource,
    cancel_study_material_job,
    claim_next_study_material_job,
    delete_document_dependent_work,
    enqueue_study_material_job,
    fail_study_material_job,
    finalize_study_flashcard_job,
    finalize_study_material_job,
    recover_expired_study_material_jobs,
    renew_study_material_lease,
)
from app.services.ai_retrieval import retrieve_passages
from app.services.study_material_generation import list_study_flashcard_materials
from app.services.ai_usage_reservations import (
    UsageLimitExceeded,
    mark_dispatching,
    recover_expired_reservations,
    reserve_usage,
    settle_usage,
)
from app.services.document_worker import DocumentLease, _publish_result
from app.schemas.learning import StudySpaceCreate
from app.schemas.study_kits import StudyKitCreate
from app.services.study_spaces import create_study_space, delete_study_space
from app.services.study_kits import create_study_kit, delete_study_kit
from app.services.subjects import delete_subject
from app.services.documents import delete_document
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
                session.execute(
                    delete(StudyMaterialJobSource).where(
                        StudyMaterialJobSource.user_id == user_id
                    )
                )
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


def _new_material_job(
    session: Session,
    *,
    user: User,
    kit: StudyKit,
    suffix: str,
    status: str = "queued",
    attempts_used: int = 0,
    lease_token: UUID | None = None,
    lease_expires_at: datetime | None = None,
) -> StudyMaterialJob:
    job = StudyMaterialJob(
        user_id=user.id,
        study_space_id=kit.study_space_id,
        study_kit_id=kit.id,
        kind="notes",
        status=status,
        attempts_used=attempts_used,
        lease_token=lease_token,
        lease_expires_at=lease_expires_at,
        idempotency_key=f"{suffix}-{uuid4().hex}",
        request_hash="e" * 64,
        request_payload={},
    )
    session.add(job)
    session.flush()
    return job


def _lock_unrelated_study_material_jobs(
    session: Session, excluded_job_ids: set[UUID]
) -> None:
    session.execute(text("SET LOCAL lock_timeout = '8s'"))
    statement = select(StudyMaterialJob).order_by(StudyMaterialJob.id)
    if excluded_job_ids:
        statement = statement.where(StudyMaterialJob.id.notin_(excluded_job_ids))
    session.scalars(statement.with_for_update()).all()


def _add_material_source(
    session: Session,
    *,
    user: User,
    kit: StudyKit,
    job: StudyMaterialJob,
    document: Document,
    generation: DocumentIndexGeneration,
) -> None:
    session.add(
        StudyMaterialJobSource(
            job_id=job.id,
            document_id=document.id,
            user_id=user.id,
            study_space_id=kit.study_space_id,
            generation_id=generation.id,
            source_document_sha256=document.content_sha256,
        )
    )
    session.flush()


def _create_exhausted_index_job(
    session: Session, *, user: User, suffix: str
) -> tuple[Document, DocumentIndexGeneration, DocumentIndexJob]:
    document, _, _ = _create_document(session, user=user, suffix=suffix)
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
        status="building",
    )
    session.add(generation)
    session.flush()
    job = DocumentIndexJob(
        document_id=document.id,
        generation_id=generation.id,
        status="queued",
        attempts_used=5,
    )
    session.add(job)
    session.flush()
    return document, generation, job


def _wait_for_postgres_lock_wait(
    session_factory: sessionmaker[Session],
    backend_pid: int,
    blocking_backend_pid: int,
    *,
    timeout: float = 5,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with session_factory() as session:
            waiting = session.scalar(
                text(
                    "SELECT wait_event_type = 'Lock' "
                    "AND :blocking_pid = ANY(pg_blocking_pids(pid)) "
                    "FROM pg_stat_activity WHERE pid = :pid"
                ).bindparams(pid=backend_pid, blocking_pid=blocking_backend_pid)
            )
        if waiting:
            return True
        time.sleep(0.01)
    return False


def _postgres_lock_dependency_diagnostics(
    session_factory: sessionmaker[Session],
    backend_pids: set[int],
    lock_holder_pid: int,
) -> dict[int, dict[str, object]]:
    if not backend_pids:
        return {}

    root_values = ", ".join(
        f"(CAST(:root_{index} AS integer))"
        for index, _ in enumerate(sorted(backend_pids))
    )
    parameters = {
        f"root_{index}": backend_pid
        for index, backend_pid in enumerate(sorted(backend_pids))
    }
    query = text(
        "WITH RECURSIVE roots(root_pid) AS ("
        f"VALUES {root_values}"
        "), blocker_chain(root_pid, pid, path, cycle) AS ("
        "SELECT root_pid::integer, root_pid::integer, "
        "ARRAY[root_pid::integer]::integer[], false FROM roots "
        "UNION ALL "
        "SELECT chain.root_pid::integer, blocker.pid::integer, "
        "(chain.path || blocker.pid::integer)::integer[], "
        "blocker.pid::integer = ANY(chain.path) "
        "FROM blocker_chain AS chain "
        "CROSS JOIN LATERAL unnest(pg_blocking_pids(chain.pid)) AS blocker(pid) "
        "WHERE NOT chain.cycle "
        "AND chain.pid <> :lock_holder_pid "
        "AND cardinality(chain.path) < 32"
        ") "
        "SELECT chain.root_pid, chain.pid, chain.path, chain.cycle, "
        "activity.pid, activity.wait_event_type, pg_blocking_pids(chain.pid) "
        "FROM blocker_chain AS chain "
        "LEFT JOIN pg_stat_activity AS activity ON activity.pid = chain.pid"
    )
    parameters["lock_holder_pid"] = lock_holder_pid
    with session_factory() as session:
        rows = session.execute(query, parameters).all()

    chains: dict[
        int, list[tuple[int, list[int], bool, bool, str | None, list[int]]]
    ] = {backend_pid: [] for backend_pid in backend_pids}
    for root_pid, pid, path, cycle, activity_pid, wait_event_type, blockers in rows:
        chains.setdefault(root_pid, []).append(
            (
                pid,
                list(path),
                cycle,
                activity_pid is None,
                wait_event_type,
                list(blockers or []),
            )
        )

    diagnostics: dict[int, dict[str, object]] = {}
    for backend_pid, entries in chains.items():
        root = next((entry for entry in entries if entry[1] == [backend_pid]), None)
        cycles = [entry[1] for entry in entries if entry[2]]
        missing_activity = [entry[1] for entry in entries if entry[3]]
        missing_terminal = [
            entry[1]
            for entry in entries
            if entry[0] != lock_holder_pid and not entry[5]
        ]
        truncated = [
            entry[1]
            for entry in entries
            if entry[0] != lock_holder_pid
            and len(entry[1]) >= 32
            and entry[5]
        ]
        terminal_paths = [
            entry[1]
            for entry in entries
            if entry[0] == lock_holder_pid
        ]
        diagnostics[backend_pid] = {
            "expected_holder_pid": lock_holder_pid,
            "wait_event_type": root[4] if root is not None else None,
            "direct_blocker_pids": root[5] if root is not None else [],
            "holder_paths": terminal_paths,
            "cycles": cycles,
            "missing_activity_paths": missing_activity,
            "paths_ending_without_holder": missing_terminal,
            "truncated_paths": truncated,
            "blocked_on_expected_holder": (
                root is not None
                and root[4] == "Lock"
                and bool(terminal_paths)
                and not cycles
                and not missing_activity
                and not missing_terminal
                and not truncated
            ),
        }
    return diagnostics


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


def test_study_material_provenance_constraints_and_database_cascades(
    ai_test_factory,
) -> None:
    with ai_test_factory() as session:
        transaction = session.begin()
        try:
            owner = _create_user(session, "study-material-owner")
            foreign_owner = _create_user(session, "study-material-foreign")
            document, subject, study_space = _create_document(
                session, user=owner, suffix="material-source"
            )
            other_space_document, other_subject, other_space = _create_document(
                session, user=owner, suffix="material-other-space"
            )
            foreign_document, _, _ = _create_document(
                session, user=foreign_owner, suffix="material-foreign"
            )
            generation = _create_ready_index(session, document)
            other_space_generation = _create_ready_index(session, other_space_document)
            foreign_generation = _create_ready_index(session, foreign_document)

            kit = StudyKit(
                user_id=owner.id,
                subject_id=subject.id,
                study_space_id=study_space.id,
                title="Source kit",
            )
            other_kit = StudyKit(
                user_id=owner.id,
                subject_id=other_subject.id,
                study_space_id=other_space.id,
                title="Other-space kit",
            )
            session.add_all([kit, other_kit])
            session.flush()

            job = StudyMaterialJob(
                user_id=owner.id,
                study_space_id=study_space.id,
                study_kit_id=kit.id,
                kind="notes",
                status="queued",
                attempts_used=0,
                idempotency_key=f"study-material-{uuid4().hex}",
                request_hash="b" * 64,
                request_payload={},
            )
            other_job = StudyMaterialJob(
                user_id=owner.id,
                study_space_id=other_space.id,
                study_kit_id=other_kit.id,
                kind="notes",
                status="queued",
                attempts_used=0,
                idempotency_key=f"study-material-{uuid4().hex}",
                request_hash="c" * 64,
                request_payload={},
            )
            session.add_all([job, other_job])
            session.flush()

            material = StudyMaterial(
                user_id=owner.id,
                study_kit_id=kit.id,
                job_id=job.id,
                kind="notes",
                version_number=1,
                title="Immutable notes",
                content={"sections": []},
            )
            other_material = StudyMaterial(
                user_id=owner.id,
                study_kit_id=other_kit.id,
                job_id=other_job.id,
                kind="notes",
                version_number=1,
                title="Other notes",
                content={"sections": []},
            )
            session.add_all([material, other_material])
            session.flush()
            assert material.supersedes_material_id is None
            assert material.supersedes_study_kit_id is None

            source = StudyMaterialJobSource(
                job_id=job.id,
                document_id=document.id,
                user_id=owner.id,
                study_space_id=study_space.id,
                generation_id=generation.id,
                source_document_sha256=document.content_sha256,
            )
            session.add(source)
            session.flush()
            citation = StudyMaterialCitation(
                study_material_id=material.id,
                job_id=job.id,
                citation_key="source-1",
                document_id=document.id,
                generation_id=generation.id,
                chunk_index=0,
            )
            session.add(citation)
            session.flush()

            invalid_sources = (
                StudyMaterialJobSource(
                    job_id=job.id,
                    document_id=other_space_document.id,
                    user_id=owner.id,
                    study_space_id=study_space.id,
                    generation_id=other_space_generation.id,
                    source_document_sha256=other_space_document.content_sha256,
                ),
                StudyMaterialJobSource(
                    job_id=job.id,
                    document_id=foreign_document.id,
                    user_id=owner.id,
                    study_space_id=study_space.id,
                    generation_id=foreign_generation.id,
                    source_document_sha256=foreign_document.content_sha256,
                ),
            )
            for invalid_source in invalid_sources:
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.add(invalid_source)
                        session.flush()

            invalid_citation = StudyMaterialCitation(
                study_material_id=material.id,
                job_id=job.id,
                citation_key="not-in-source-set",
                document_id=other_space_document.id,
                generation_id=other_space_generation.id,
                chunk_index=0,
            )
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(invalid_citation)
                    session.flush()

            supersession_job = StudyMaterialJob(
                user_id=owner.id,
                study_space_id=study_space.id,
                study_kit_id=kit.id,
                kind="notes",
                status="queued",
                attempts_used=0,
                idempotency_key=f"study-material-{uuid4().hex}",
                request_hash="d" * 64,
                request_payload={},
            )
            session.add(supersession_job)
            session.flush()
            valid_supersession = StudyMaterial(
                user_id=owner.id,
                study_kit_id=kit.id,
                job_id=supersession_job.id,
                kind="notes",
                version_number=2,
                title="Same-kit revision",
                content={"sections": []},
                supersedes_material_id=material.id,
                supersedes_study_kit_id=kit.id,
            )
            session.add(valid_supersession)
            session.flush()

            invalid_supersession_job = StudyMaterialJob(
                user_id=owner.id,
                study_space_id=study_space.id,
                study_kit_id=kit.id,
                kind="notes",
                status="queued",
                attempts_used=0,
                idempotency_key=f"study-material-{uuid4().hex}",
                request_hash="e" * 64,
                request_payload={},
            )
            session.add(invalid_supersession_job)
            session.flush()
            cross_kit_supersession = StudyMaterial(
                user_id=owner.id,
                study_kit_id=kit.id,
                job_id=invalid_supersession_job.id,
                kind="notes",
                version_number=3,
                title="Invalid cross-kit supersession",
                content={"sections": []},
                supersedes_material_id=other_material.id,
                supersedes_study_kit_id=other_kit.id,
            )
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(cross_kit_supersession)
                    session.flush()

            cross_kit_supersession_missing_kit = StudyMaterial(
                user_id=owner.id,
                study_kit_id=kit.id,
                job_id=invalid_supersession_job.id,
                kind="notes",
                version_number=3,
                title="Invalid cross-kit supersession without kit",
                content={"sections": []},
                supersedes_material_id=other_material.id,
                supersedes_study_kit_id=None,
            )
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(cross_kit_supersession_missing_kit)
                    session.flush()

            supersession_kit_without_material = StudyMaterial(
                user_id=owner.id,
                study_kit_id=kit.id,
                job_id=invalid_supersession_job.id,
                kind="notes",
                version_number=3,
                title="Invalid supersession kit without material",
                content={"sections": []},
                supersedes_material_id=None,
                supersedes_study_kit_id=kit.id,
            )
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(supersession_kit_without_material)
                    session.flush()

            reservation = AIUsageReservation(
                user_id=owner.id,
                document_id=document.id,
                operation="study_notes",
                idempotency_key=f"study-material-usage-{uuid4().hex}",
                reserved_input_tokens=100,
                reserved_output_tokens=100,
                reserved_cost_usd=Decimal("0.001"),
                pricing_version="test-pricing-v1",
                model_name="test-model",
                profile_id=PROFILE_ID,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            session.add(reservation)
            session.flush()
            usage_event = AIUsageEvent(
                user_id=owner.id,
                document_id=document.id,
                reservation_id=reservation.id,
                operation="study_notes",
                model_name="test-model",
                profile_id=PROFILE_ID,
                input_tokens=100,
                output_tokens=50,
                estimated_cost_usd=Decimal("0.001"),
                pricing_version="test-pricing-v1",
                accounting_basis="conservative_estimate",
                idempotency_key=f"study-material-event-{uuid4().hex}",
            )
            session.add(usage_event)
            session.flush()

            restricted_deletes = (
                delete(Document).where(Document.id == document.id),
                delete(DocumentIndexGeneration).where(
                    DocumentIndexGeneration.id == generation.id
                ),
                delete(DocumentChunk).where(
                    DocumentChunk.document_id == document.id,
                    DocumentChunk.generation_id == generation.id,
                    DocumentChunk.chunk_index == 0,
                ),
            )
            for statement in restricted_deletes:
                with pytest.raises(IntegrityError):
                    with session.begin_nested():
                        session.execute(statement)

            kit_id = kit.id
            job_id = job.id
            material_id = material.id
            other_kit_id = other_kit.id
            source_document_id = document.id
            reservation_id = reservation.id
            usage_event_id = usage_event.id
            session.execute(
                text("DELETE FROM study_kits WHERE id = :kit_id"),
                {"kit_id": kit_id},
            )

            assert session.scalar(
                text("SELECT count(*) FROM study_kits WHERE id = :id"),
                {"id": kit_id},
            ) == 0
            assert session.scalar(
                text("SELECT count(*) FROM study_material_jobs WHERE study_kit_id = :id"),
                {"id": kit_id},
            ) == 0
            assert session.scalar(
                text("SELECT count(*) FROM study_material_job_sources WHERE job_id = :id"),
                {"id": job_id},
            ) == 0
            assert session.scalar(
                text("SELECT count(*) FROM study_materials WHERE study_kit_id = :id"),
                {"id": kit_id},
            ) == 0
            assert session.scalar(
                text(
                    "SELECT count(*) FROM study_material_citations "
                    "WHERE study_material_id = :id"
                ),
                {"id": material_id},
            ) == 0
            assert session.scalar(
                text("SELECT count(*) FROM study_kits WHERE id = :id"),
                {"id": other_kit_id},
            ) == 1
            assert session.scalar(
                text("SELECT count(*) FROM study_materials WHERE study_kit_id = :id"),
                {"id": other_kit_id},
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(AIUsageReservation).where(
                    AIUsageReservation.id == reservation_id
                )
            ) == 1
            assert session.scalar(
                select(func.count()).select_from(AIUsageEvent).where(
                    AIUsageEvent.id == usage_event_id
                )
            ) == 1
            assert session.get(Document, source_document_id) is not None
        finally:
            if transaction.is_active:
                transaction.rollback()


def test_flashcard_migration_downgrade_and_upgrade_restore_kind_constraints(
    ai_test_factory,
) -> None:
    migration_path = BACKEND_ROOT / "alembic" / "versions" / "20261110_08_flashcards.py"
    spec = importlib.util.spec_from_file_location(
        "flashcard_migration_under_test", migration_path
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    user_id: UUID | None = None
    expected_flashcard_data: dict[str, int] = {}
    with ai_test_factory() as session:
        engine = session.get_bind()
        with session.begin():
            user = _create_user(session, "flashcard-migration-roundtrip")
            document, subject, space = _create_document(
                session, user=user, suffix="flashcard-migration-roundtrip"
            )
            generation = _create_ready_index(session, document)
            kit = StudyKit(
                user_id=user.id,
                subject_id=subject.id,
                study_space_id=space.id,
                title="Migration round-trip kit",
            )
            session.add(kit)
            session.flush()
            flashcard_job = StudyMaterialJob(
                user_id=user.id,
                study_space_id=space.id,
                study_kit_id=kit.id,
                kind="flashcards",
                status="completed",
                attempts_used=1,
                idempotency_key=f"migration-flashcards-{uuid4().hex}",
                request_hash="a" * 64,
                request_payload={"card_count": 1},
            )
            notes_job = StudyMaterialJob(
                user_id=user.id,
                study_space_id=space.id,
                study_kit_id=kit.id,
                kind="notes",
                status="completed",
                attempts_used=1,
                idempotency_key=f"migration-notes-{uuid4().hex}",
                request_hash="b" * 64,
                request_payload={"detail_level": "standard"},
            )
            session.add_all([flashcard_job, notes_job])
            session.flush()
            _add_material_source(
                session,
                user=user,
                kit=kit,
                job=flashcard_job,
                document=document,
                generation=generation,
            )
            flashcard_material = StudyMaterial(
                user_id=user.id,
                study_kit_id=kit.id,
                job_id=flashcard_job.id,
                kind="flashcards",
                version_number=1,
                title="Migration test deck",
                content={"card_count": 1},
            )
            notes_material = StudyMaterial(
                user_id=user.id,
                study_kit_id=kit.id,
                job_id=notes_job.id,
                kind="notes",
                version_number=1,
                title="Migration test notes",
                content={"sections": []},
            )
            session.add_all([flashcard_material, notes_material])
            session.flush()
            citation = StudyMaterialCitation(
                study_material_id=flashcard_material.id,
                job_id=flashcard_job.id,
                citation_key="P0001",
                document_id=document.id,
                generation_id=generation.id,
                chunk_index=0,
            )
            card = StudyMaterialCard(
                user_id=user.id,
                material_id=flashcard_material.id,
                position=0,
                front="What does the source explain?",
                back="It explains a study concept.",
            )
            session.add_all([citation, card])
            session.flush()
            session.add(
                StudyMaterialCardCitation(
                    material_id=flashcard_material.id,
                    card_id=card.id,
                    citation_id=citation.id,
                )
            )
            session.add(
                FlashcardReviewEvent(
                    user_id=user.id,
                    card_id=card.id,
                    rating="remembered",
                    idempotency_key=f"migration-review-{uuid4().hex}",
                )
            )
            session.flush()
            user_id = user.id
            expected_flashcard_data = {
                "jobs": int(
                    session.scalar(
                        select(func.count()).select_from(StudyMaterialJob).where(
                            StudyMaterialJob.user_id == user.id,
                            StudyMaterialJob.kind == "flashcards",
                        )
                    )
                    or 0
                ),
                "cards": int(
                    session.scalar(
                        select(func.count()).select_from(StudyMaterialCard).where(
                            StudyMaterialCard.user_id == user.id
                        )
                    )
                    or 0
                ),
                "citations": int(
                    session.scalar(
                        select(func.count())
                        .select_from(StudyMaterialCardCitation)
                        .where(
                            StudyMaterialCardCitation.material_id
                            == flashcard_material.id
                        )
                    )
                    or 0
                ),
                "reviews": int(
                    session.scalar(
                        select(func.count()).select_from(FlashcardReviewEvent).where(
                            FlashcardReviewEvent.user_id == user.id
                        )
                    )
                    or 0
                ),
            }

    try:
        assert all(count > 0 for count in expected_flashcard_data.values())
        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                # The migration downgrade is exercised in a rolled-back transaction
                # so the fixture database remains at head for other integration tests.
                earlier_tables = [
                    "study_material_jobs",
                    "study_materials",
                    "study_material_citations",
                ]
                migration_added_earlier_constraints = {
                    "uq_study_materials_user_id_id",
                    "uq_study_materials_id_kind",
                    "uq_study_material_citations_material_id_id",
                }
                original_earlier_constraints = set(
                    connection.scalars(
                        text(
                            "SELECT c.conname FROM pg_constraint AS c "
                            "JOIN pg_class AS r ON r.oid = c.conrelid "
                            "JOIN pg_namespace AS n ON n.oid = r.relnamespace "
                            "WHERE n.nspname = current_schema() "
                            "AND r.relname = ANY(:tables)"
                        ),
                        {"tables": earlier_tables},
                    )
                )
                original_earlier_indexes = set(
                    connection.scalars(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = current_schema() "
                            "AND tablename = ANY(:tables)"
                        ),
                        {"tables": earlier_tables},
                    )
                )
                with Operations.context(MigrationContext.configure(connection)):
                    migration.downgrade()

                flashcard_jobs_remaining = connection.scalar(
                    text(
                        "SELECT count(*) FROM study_material_jobs "
                        "WHERE kind = 'flashcards' AND user_id = :user_id"
                    ),
                    {"user_id": user_id},
                )
                assert flashcard_jobs_remaining == 0
                assert connection.scalar(
                    text(
                        "SELECT count(*) FROM study_material_jobs "
                        "WHERE kind = 'notes' AND user_id = :user_id"
                    ),
                    {"user_id": user_id},
                ) == 1

                assert connection.scalar(
                    text("SELECT to_regclass('study_material_cards') IS NULL")
                )
                assert connection.scalar(
                    text("SELECT to_regclass('study_material_card_citations') IS NULL")
                )
                assert connection.scalar(
                    text("SELECT to_regclass('flashcard_review_events') IS NULL")
                )
                assert connection.scalar(
                    text(
                        "SELECT count(*) FROM pg_indexes "
                        "WHERE schemaname = current_schema() "
                        "AND tablename IN ('study_material_cards', "
                        "'study_material_card_citations', "
                        "'flashcard_review_events')"
                    )
                ) == 0

                trigger_names = {
                    "flashcard_review_events_no_update",
                    "flashcard_card_requires_citation",
                    "flashcard_citation_set_remains_nonempty",
                }
                remaining_triggers = set(
                    connection.scalars(
                        text(
                            "SELECT t.tgname "
                            "FROM pg_trigger AS t "
                            "JOIN pg_class AS r ON r.oid = t.tgrelid "
                            "JOIN pg_namespace AS n ON n.oid = r.relnamespace "
                            "WHERE n.nspname = current_schema() "
                            "AND NOT t.tgisinternal AND t.tgname = ANY(:names)"
                        ),
                        {"names": list(trigger_names)},
                    )
                )
                assert remaining_triggers == set()

                function_names = {
                    "prevent_flashcard_review_event_update",
                    "require_flashcard_card_citation",
                    "require_flashcard_citation_set_nonempty",
                }
                remaining_functions = set(
                    connection.scalars(
                        text(
                            "SELECT p.proname "
                            "FROM pg_proc AS p "
                            "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                            "WHERE n.nspname = current_schema() "
                            "AND p.proname = ANY(:names)"
                        ),
                        {"names": list(function_names)},
                    )
                )
                assert remaining_functions == set()

                migration_constraints = {
                    "uq_study_materials_user_id_id",
                    "uq_study_materials_id_kind",
                    "uq_study_material_citations_material_id_id",
                    "ck_study_material_cards_material_kind",
                    "ck_study_material_cards_position",
                    "ck_study_material_cards_content_nonblank",
                    "ck_study_material_cards_difficulty",
                    "fk_study_material_cards_user_material",
                    "fk_study_material_cards_material_kind",
                    "uq_study_material_cards_material_position",
                    "uq_study_material_cards_material_id_id",
                    "uq_study_material_cards_user_id_id",
                    "fk_study_material_card_citations_material_card",
                    "fk_study_material_card_citations_material_citation",
                    "uq_study_material_card_citations_card_citation",
                    "ck_flashcard_review_events_rating",
                    "fk_flashcard_review_events_user_card",
                    "uq_flashcard_review_events_user_idempotency",
                }
                remaining_constraints = set(
                    connection.scalars(
                        text(
                            "SELECT c.conname "
                            "FROM pg_constraint AS c "
                            "JOIN pg_namespace AS n ON n.oid = c.connamespace "
                            "WHERE n.nspname = current_schema() "
                            "AND c.conname = ANY(:names)"
                        ),
                        {"names": list(migration_constraints)},
                    )
                )
                assert remaining_constraints == set()
                downgraded_earlier_constraints = set(
                    connection.scalars(
                        text(
                            "SELECT c.conname FROM pg_constraint AS c "
                            "JOIN pg_class AS r ON r.oid = c.conrelid "
                            "JOIN pg_namespace AS n ON n.oid = r.relnamespace "
                            "WHERE n.nspname = current_schema() "
                            "AND r.relname = ANY(:tables)"
                        ),
                        {"tables": earlier_tables},
                    )
                )
                assert downgraded_earlier_constraints == (
                    original_earlier_constraints
                    - migration_added_earlier_constraints
                )
                downgraded_earlier_indexes = set(
                    connection.scalars(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = current_schema() "
                            "AND tablename = ANY(:tables)"
                        ),
                        {"tables": earlier_tables},
                    )
                )
                assert downgraded_earlier_indexes == (
                    original_earlier_indexes
                    - migration_added_earlier_constraints
                )

                migration_indexes = {
                    "ix_study_material_card_citations_material",
                    "ix_flashcard_review_events_user_card",
                    "ix_flashcard_review_events_card_reviewed",
                }
                remaining_indexes = set(
                    connection.scalars(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = current_schema() "
                            "AND indexname = ANY(:names)"
                        ),
                        {"names": list(migration_indexes)},
                    )
                )
                assert remaining_indexes == set()

                for earlier_table in (
                    "study_material_jobs",
                    "study_materials",
                    "study_material_citations",
                    "documents",
                ):
                    assert connection.scalar(
                        text("SELECT to_regclass(:table_name) IS NOT NULL"),
                        {"table_name": earlier_table},
                    )

                for table_name, constraint_name in (
                    ("study_materials", "ck_study_materials_kind"),
                    ("study_material_jobs", "ck_study_material_jobs_kind"),
                ):
                    definition = connection.scalar(
                        text(
                            "SELECT pg_get_constraintdef(constraint_row.oid) "
                            "FROM pg_constraint AS constraint_row "
                            "WHERE constraint_row.conname = :constraint_name "
                            "AND constraint_row.conrelid = "
                            "to_regclass(:table_name)"
                        ),
                        {
                            "constraint_name": constraint_name,
                            "table_name": table_name,
                        },
                    )
                    assert definition is not None
                    assert "notes" in definition
                    assert "flashcards" not in definition

                assert connection.scalar(
                    text(
                        "SELECT count(*) FROM study_materials "
                        "WHERE kind = 'notes' AND title = 'Migration test notes'"
                    )
                ) == 1

                with Operations.context(MigrationContext.configure(connection)):
                    migration.upgrade()

                trigger_bindings = {
                    row[0]: (row[1], row[2].lower(), row[3])
                    for row in connection.execute(
                        text(
                            "SELECT t.tgname, r.relname, pg_get_triggerdef(t.oid), "
                            "p.proname "
                            "FROM pg_trigger AS t "
                            "JOIN pg_class AS r ON r.oid = t.tgrelid "
                            "JOIN pg_namespace AS n ON n.oid = r.relnamespace "
                            "JOIN pg_proc AS p ON p.oid = t.tgfoid "
                            "WHERE n.nspname = current_schema() "
                            "AND NOT t.tgisinternal "
                            "AND t.tgname = ANY(:names)"
                        ),
                        {"names": list(trigger_names)},
                    )
                }
                assert set(trigger_bindings) == trigger_names
                assert {
                    name: (binding[0], binding[2])
                    for name, binding in trigger_bindings.items()
                } == {
                    "flashcard_review_events_no_update": (
                        "flashcard_review_events",
                        "prevent_flashcard_review_event_update",
                    ),
                    "flashcard_card_requires_citation": (
                        "study_material_cards",
                        "require_flashcard_card_citation",
                    ),
                    "flashcard_citation_set_remains_nonempty": (
                        "study_material_card_citations",
                        "require_flashcard_citation_set_nonempty",
                    ),
                }
                review_trigger_definition = trigger_bindings[
                    "flashcard_review_events_no_update"
                ][1]
                assert "before delete or update" in review_trigger_definition
                assert "after insert on" in trigger_bindings[
                    "flashcard_card_requires_citation"
                ][1]
                assert "deferrable initially deferred" in trigger_bindings[
                    "flashcard_card_requires_citation"
                ][1]
                citation_trigger_definition = trigger_bindings[
                    "flashcard_citation_set_remains_nonempty"
                ][1]
                assert "after insert" in citation_trigger_definition
                assert "or update" in citation_trigger_definition
                assert "or delete" in citation_trigger_definition
                assert "deferrable initially deferred" in citation_trigger_definition

                for table_name in (
                    "study_material_cards",
                    "study_material_card_citations",
                    "flashcard_review_events",
                ):
                    assert connection.scalar(
                        text("SELECT to_regclass(:table_name) IS NOT NULL"),
                        {"table_name": table_name},
                    )
                for name in trigger_names:
                    assert connection.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_trigger AS t "
                            "JOIN pg_class AS r ON r.oid = t.tgrelid "
                            "JOIN pg_namespace AS n ON n.oid = r.relnamespace "
                            "WHERE n.nspname = current_schema() "
                            "AND NOT t.tgisinternal AND t.tgname = :name)"
                        ),
                        {"name": name},
                    )
                for name in function_names:
                    assert connection.scalar(
                        text(
                            "SELECT EXISTS ("
                            "SELECT 1 FROM pg_proc AS p "
                            "JOIN pg_namespace AS n ON n.oid = p.pronamespace "
                            "WHERE n.nspname = current_schema() "
                            "AND p.proname = :name)"
                        ),
                        {"name": name},
                    )
                restored_constraints = set(
                    connection.scalars(
                        text(
                            "SELECT conname FROM pg_constraint "
                            "WHERE connamespace = current_schema()::regnamespace "
                            "AND conname = ANY(:names)"
                        ),
                        {"names": list(migration_constraints)},
                    )
                )
                assert restored_constraints == migration_constraints
                restored_indexes = set(
                    connection.scalars(
                        text(
                            "SELECT indexname FROM pg_indexes "
                            "WHERE schemaname = current_schema() "
                            "AND indexname = ANY(:names)"
                        ),
                        {"names": list(migration_indexes)},
                    )
                )
                assert restored_indexes == migration_indexes

                upgraded_kind = connection.scalar(
                    text(
                        "SELECT pg_get_constraintdef(constraint_row.oid) "
                        "FROM pg_constraint AS constraint_row "
                        "WHERE constraint_row.conname = 'ck_study_materials_kind' "
                        "AND constraint_row.conrelid = "
                        "to_regclass('study_materials')"
                    )
                )
                assert upgraded_kind is not None
                assert "notes" in upgraded_kind and "flashcards" in upgraded_kind
                composite_fk = connection.scalar(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_constraint "
                        "WHERE conname = 'fk_study_material_cards_material_kind' "
                        "AND contype = 'f' "
                        "AND conrelid = to_regclass('study_material_cards'))"
                    )
                )
                assert composite_fk
            finally:
                transaction.rollback()
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="flashcard migration downgrade and upgrade",
        )


def test_flashcard_material_kind_composite_foreign_key_rejects_notes_material(
    ai_test_factory,
) -> None:
    user_id: UUID | None = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "flashcard-kind-fk")
            _, subject, space = _create_document(
                session, user=user, suffix="flashcard-kind-fk"
            )
            kit = StudyKit(
                user_id=user.id,
                subject_id=subject.id,
                study_space_id=space.id,
                title="Material kind FK kit",
            )
            session.add(kit)
            session.flush()
            notes_job = StudyMaterialJob(
                user_id=user.id,
                study_space_id=space.id,
                study_kit_id=kit.id,
                kind="notes",
                status="completed",
                attempts_used=1,
                idempotency_key=f"notes-kind-fk-{uuid4().hex}",
                request_hash="c" * 64,
                request_payload={"detail_level": "standard"},
            )
            session.add(notes_job)
            session.flush()
            notes_material = StudyMaterial(
                user_id=user.id,
                study_kit_id=kit.id,
                job_id=notes_job.id,
                kind="notes",
                version_number=1,
                title="Notes for FK test",
                content={"sections": []},
            )
            session.add(notes_material)
            session.flush()
            user_id = user.id
            notes_material_id = notes_material.id

    try:
        with ai_test_factory() as session:
            with pytest.raises(IntegrityError) as raised:
                with session.begin_nested():
                    session.add(
                        StudyMaterialCard(
                            user_id=user_id,
                            material_id=notes_material_id,
                            position=0,
                            front="Invalid notes association",
                            back="The material kind is notes.",
                        )
                    )
                    session.flush()

            assert (
                raised.value.orig.diag.constraint_name
                == "fk_study_material_cards_material_kind"
            )
            assert session.scalar(
                select(func.count()).select_from(StudyMaterialCard).where(
                    StudyMaterialCard.material_id == notes_material_id
                )
            ) == 0
            assert session.get(StudyMaterial, notes_material_id) is not None
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="flashcard material kind composite foreign key",
        )


def test_flashcard_finalization_constraints_reviews_and_progress(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        ai_embedding_profile_id=PROFILE_ID,
        document_worker_lease_seconds=60,
    )
    monkeypatch.setattr(study_material_jobs, "get_settings", lambda: settings)
    user_id = None
    foreign_user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "flashcards")
            document, subject, space = _create_document(
                session, user=user, suffix="flashcards-source"
            )
            generation = _create_ready_index(session, document)
            kit = StudyKit(
                user_id=user.id,
                subject_id=subject.id,
                study_space_id=space.id,
                title="Flashcard kit",
            )
            session.add(kit)
            session.flush()
            job = StudyMaterialJob(
                user_id=user.id,
                study_space_id=space.id,
                study_kit_id=kit.id,
                kind="flashcards",
                status="queued",
                attempts_used=0,
                idempotency_key=f"flashcards-{uuid4().hex}",
                request_hash="a" * 64,
                request_payload={"card_count": 1},
            )
            session.add(job)
            session.flush()
            _add_material_source(
                session,
                user=user,
                kit=kit,
                job=job,
                document=document,
                generation=generation,
            )
            user_id = user.id
            job_id = job.id
            document_id = document.id
            generation_id = generation.id

    try:
        lease = claim_next_study_material_job(ai_test_factory)
        assert lease is not None and lease.job_id == job_id and lease.kind == "flashcards"
        citation = StudyMaterialCitationInput(
            citation_key="P0001",
            document_id=document_id,
            generation_id=generation_id,
            chunk_index=0,
        )
        with pytest.raises(IntegrityError):
            finalize_study_flashcard_job(
                ai_test_factory,
                lease,
                title="Rejected partial deck",
                content={"card_count": 1},
                citations=(citation,),
                cards=(
                    StudyMaterialCardInput(
                        position=0,
                        front="Invalid difficulty",
                        back="This must roll back.",
                        explanation=None,
                        difficulty="invalid",
                        tags=(),
                        citation_keys=("P0001",),
                    ),
                ),
            )
        with ai_test_factory() as session:
            assert session.scalar(
                select(func.count()).select_from(StudyMaterial).where(
                    StudyMaterial.job_id == job_id
                )
            ) == 0
            assert session.get(StudyMaterialJob, job_id).status == "processing"
        assert finalize_study_flashcard_job(
            ai_test_factory,
            lease,
            title="Source-grounded deck",
            content={"card_count": 1},
            citations=(citation,),
            cards=(
                StudyMaterialCardInput(
                    position=0,
                    front="What does the source explain?",
                    back="It explains the learning concept.",
                    explanation=None,
                    difficulty="easy",
                    tags=("fundamentals",),
                    citation_keys=("P0001",),
                ),
            ),
        )
    except Exception:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=True,
            description="flashcard finalization and review",
        )
        _cleanup_test_user(
            ai_test_factory,
            foreign_user_id,
            test_failed=True,
            description="flashcard deck ownership",
        )
        raise

    try:
        with ai_test_factory() as session:
            material = session.scalar(
                select(StudyMaterial).where(StudyMaterial.job_id == job_id)
            )
            assert material is not None and material.kind == "flashcards"
            card = session.scalar(
                select(StudyMaterialCard).where(
                    StudyMaterialCard.material_id == material.id
                )
            )
            assert card is not None and card.position == 0
            assert card.material_id == material.id
            assert material.kind == "flashcards"
            citation_link = session.scalar(
                select(StudyMaterialCardCitation).where(
                    StudyMaterialCardCitation.card_id == card.id
                )
            )
            assert citation_link is not None
            citation_row = session.get(StudyMaterialCitation, citation_link.citation_id)
            assert citation_row is not None
            assert citation_row.study_material_id == material.id
            assert citation_row.generation_id == generation_id

            owner = session.get(User, user_id)
            assert owner is not None
            other_job = StudyMaterialJob(
                user_id=owner.id,
                study_space_id=lease.study_space_id,
                study_kit_id=lease.study_kit_id,
                kind="flashcards",
                status="completed",
                attempts_used=1,
                idempotency_key=f"other-flashcards-{uuid4().hex}",
                request_hash="b" * 64,
                request_payload={"card_count": 1},
            )
            session.add(other_job)
            session.flush()
            _add_material_source(
                session,
                user=owner,
                kit=session.get(StudyKit, lease.study_kit_id),
                job=other_job,
                document=session.get(Document, document_id),
                generation=session.get(DocumentIndexGeneration, generation_id),
            )
            other_material = StudyMaterial(
                user_id=owner.id,
                study_kit_id=lease.study_kit_id,
                job_id=other_job.id,
                kind="flashcards",
                version_number=2,
                title="Other deck",
                content={"card_count": 1},
            )
            session.add(other_material)
            session.flush()
            other_citation = StudyMaterialCitation(
                study_material_id=other_material.id,
                job_id=other_job.id,
                citation_key="P0001",
                document_id=document_id,
                generation_id=generation_id,
                chunk_index=0,
            )
            session.add(other_citation)
            session.flush()

            notes_job = StudyMaterialJob(
                user_id=owner.id,
                study_space_id=lease.study_space_id,
                study_kit_id=lease.study_kit_id,
                kind="notes",
                status="completed",
                attempts_used=1,
                idempotency_key=f"notes-material-{uuid4().hex}",
                request_hash="c" * 64,
                request_payload={"detail_level": "standard"},
            )
            session.add(notes_job)
            session.flush()
            notes_material = StudyMaterial(
                user_id=owner.id,
                study_kit_id=lease.study_kit_id,
                job_id=notes_job.id,
                kind="notes",
                version_number=1,
                title="Notes material",
                content={},
            )
            session.add(notes_material)
            session.flush()
            foreign_user = _create_user(session, "flashcards-foreign-owner")
            foreign_subject = Subject(
                user_id=foreign_user.id,
                name="Foreign flashcard subject",
            )
            session.add(foreign_subject)
            session.flush()
            foreign_space = StudySpace(
                subject_id=foreign_subject.id,
                name="Foreign flashcard space",
            )
            session.add(foreign_space)
            session.flush()
            foreign_kit = StudyKit(
                user_id=foreign_user.id,
                subject_id=foreign_subject.id,
                study_space_id=foreign_space.id,
                title="Foreign flashcard kit",
            )
            session.add(foreign_kit)
            session.flush()
            foreign_job = StudyMaterialJob(
                user_id=foreign_user.id,
                study_space_id=foreign_space.id,
                study_kit_id=foreign_kit.id,
                kind="flashcards",
                status="completed",
                attempts_used=1,
                idempotency_key=f"foreign-deck-{uuid4().hex}",
                request_hash="f" * 64,
                request_payload={"card_count": 1},
            )
            session.add(foreign_job)
            session.flush()
            foreign_material = StudyMaterial(
                user_id=foreign_user.id,
                study_kit_id=foreign_kit.id,
                job_id=foreign_job.id,
                kind="flashcards",
                version_number=1,
                title="Foreign deck",
                content={"card_count": 1},
            )
            session.add(foreign_material)
            session.flush()
            foreign_user_id = foreign_user.id
            decks, total_decks = list_study_flashcard_materials(
                session,
                user_id=owner.id,
                page=1,
                page_size=20,
            )
            assert total_decks == 2
            assert {deck.id for deck in decks} == {material.id, other_material.id}
            foreign_decks, foreign_total = list_study_flashcard_materials(
                session,
                user_id=foreign_user.id,
                page=1,
                page_size=20,
            )
            assert foreign_total == 1
            assert [deck.id for deck in foreign_decks] == [foreign_material.id]
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        StudyMaterialCard(
                            user_id=owner.id,
                            material_id=notes_material.id,
                            position=0,
                            front="Cannot belong to notes",
                            back="Database rejects this card.",
                        )
                    )
                    session.flush()

            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        StudyMaterialCard(
                            user_id=owner.id,
                            material_id=material.id,
                            position=0,
                            front="Duplicate position",
                            back="Invalid",
                        )
                    )
                    session.flush()

            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        StudyMaterialCardCitation(
                            material_id=material.id,
                            card_id=card.id,
                            citation_id=other_citation.id,
                        )
                    )
                    session.flush()

            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        StudyMaterialCard(
                            user_id=owner.id,
                            material_id=material.id,
                            position=1,
                            front="No source",
                            back="This card has no citation",
                        )
                    )
                    session.flush()
                    session.execute(
                        text(
                            "SET CONSTRAINTS flashcard_card_requires_citation IMMEDIATE"
                        )
                    )

            with pytest.raises(IntegrityError) as missing_citation:
                with session.begin_nested():
                    session.execute(
                        delete(StudyMaterialCardCitation).where(
                            StudyMaterialCardCitation.material_id == material.id,
                            StudyMaterialCardCitation.card_id == card.id,
                            StudyMaterialCardCitation.citation_id
                            == citation_link.citation_id,
                        )
                    )
                    session.execute(
                        text(
                            "SET CONSTRAINTS "
                            "flashcard_citation_set_remains_nonempty IMMEDIATE"
                        )
                    )
            assert missing_citation.value.orig.diag.sqlstate == "23514"
            assert session.scalar(
                select(func.count())
                .select_from(StudyMaterialCardCitation)
                .where(
                    StudyMaterialCardCitation.material_id == material.id,
                    StudyMaterialCardCitation.card_id == card.id,
                )
            ) == 1

            card_id = card.id
            material_id = material.id

        barrier = Barrier(2)

        def duplicate_review():
            barrier.wait(timeout=5)
            with ai_test_factory() as session:
                return create_review_event(
                    session,
                    user_id=user_id,
                    material_id=material_id,
                    card_id=card_id,
                    rating="remembered",
                    idempotency_key="same-concurrent-review",
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            duplicate_events = list(executor.map(lambda _: duplicate_review(), range(2)))
        assert duplicate_events[0].id == duplicate_events[1].id

        conflicting_barrier = Barrier(2)

        def conflicting_review(rating: str):
            conflicting_barrier.wait(timeout=5)
            with ai_test_factory() as session:
                try:
                    return create_review_event(
                        session,
                        user_id=user_id,
                        material_id=material_id,
                        card_id=card_id,
                        rating=rating,
                        idempotency_key="same-key-different-rating",
                    )
                except ReviewIdempotencyConflict:
                    return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            conflicting_events = list(
                executor.map(
                    conflicting_review,
                    ("remembered", "needs_review"),
                )
            )
        assert sum(event is not None for event in conflicting_events) == 1
        with ai_test_factory() as session:
            assert session.scalar(
                select(func.count()).select_from(FlashcardReviewEvent).where(
                    FlashcardReviewEvent.user_id == user_id,
                    FlashcardReviewEvent.idempotency_key
                    == "same-key-different-rating",
                )
            ) == 1

        distinct_key_barrier = Barrier(2)

        def distinct_key_review(index: int):
            distinct_key_barrier.wait(timeout=5)
            with ai_test_factory() as session:
                return create_review_event(
                    session,
                    user_id=user_id,
                    material_id=material_id,
                    card_id=card_id,
                    rating=("remembered", "needs_review")[index],
                    idempotency_key=f"different-key-{index}",
                )

        with ThreadPoolExecutor(max_workers=2) as executor:
            distinct_key_events = list(
                executor.map(distinct_key_review, range(2))
            )
        assert len({event.id for event in distinct_key_events}) == 2

        time.sleep(0.02)
        with ai_test_factory() as session:
            latest = create_review_event(
                session,
                user_id=user_id,
                material_id=material_id,
                card_id=card_id,
                rating="needs_review",
                idempotency_key="later-review",
            )
            progress = get_flashcard_progress(
                session,
                user_id=user_id,
                material_id=material_id,
            )
            assert progress == {
                "material_id": material_id,
                "total_cards": 1,
                "new_cards": 0,
                "reviewed_cards": 1,
                "remembered_cards": 0,
                "needs_review_cards": 1,
                "total_attempts": 5,
                "latest_reviewed_at": latest.reviewed_at,
            }
            assert (
                get_flashcard_progress(
                    session,
                    user_id=uuid4(),
                    material_id=material_id,
                )
                is None
            )
            with pytest.raises(LookupError):
                create_review_event(
                    session,
                    user_id=uuid4(),
                    material_id=material_id,
                    card_id=card_id,
                    rating="remembered",
                    idempotency_key="foreign-review",
                )

        with ai_test_factory() as session:
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.execute(
                        text(
                            "UPDATE flashcard_review_events "
                            "SET rating = 'remembered' WHERE id = :event_id"
                        ),
                        {"event_id": str(latest.id)},
                    )
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.execute(
                        text(
                            "DELETE FROM flashcard_review_events WHERE id = :event_id"
                        ),
                        {"event_id": str(latest.id)},
                    )
            session.execute(
                delete(StudyMaterial).where(StudyMaterial.id == material_id)
            )
            assert session.scalar(
                select(func.count()).select_from(StudyMaterialCard).where(
                    StudyMaterialCard.material_id == material_id
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(FlashcardReviewEvent).where(
                    FlashcardReviewEvent.card_id == card_id
                )
            ) == 0
            assert session.scalar(
                select(func.count())
                .select_from(StudyMaterialCardCitation)
                .where(StudyMaterialCardCitation.material_id == material_id)
            ) == 0
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="flashcard finalization and review",
        )
        _cleanup_test_user(
            ai_test_factory,
            foreign_user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="flashcard deck ownership",
        )


def test_stale_flashcard_lease_cannot_finalize(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        ai_embedding_profile_id=PROFILE_ID,
        document_worker_lease_seconds=30,
    )
    monkeypatch.setattr(study_material_jobs, "get_settings", lambda: settings)
    user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "stale-flashcard-lease")
            document, subject, space = _create_document(
                session, user=user, suffix="stale-flashcard-source"
            )
            generation = _create_ready_index(session, document)
            kit = StudyKit(
                user_id=user.id,
                subject_id=subject.id,
                study_space_id=space.id,
                title="Stale flashcard kit",
            )
            session.add(kit)
            session.flush()
            lease_token = uuid4()
            job = StudyMaterialJob(
                user_id=user.id,
                study_space_id=space.id,
                study_kit_id=kit.id,
                kind="flashcards",
                status="processing",
                attempts_used=1,
                lease_token=lease_token,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(seconds=30),
                idempotency_key=f"stale-flashcard-{uuid4().hex}",
                request_hash="e" * 64,
                request_payload={"card_count": 1},
            )
            session.add(job)
            session.flush()
            _add_material_source(
                session,
                user=user,
                kit=kit,
                job=job,
                document=document,
                generation=generation,
            )
            lease = StudyMaterialLease(
                job_id=job.id,
                user_id=user.id,
                study_space_id=space.id,
                study_kit_id=kit.id,
                token=lease_token,
                attempts_used=job.attempts_used,
            )
            job_id = job.id
            user_id = user.id
            document_id = document.id
            generation_id = generation.id

    try:
        assert not finalize_study_flashcard_job(
            ai_test_factory,
            lease,
            title="Stale result",
            content={"card_count": 1},
            citations=(
                StudyMaterialCitationInput(
                    "P0001", document_id, generation_id, 0
                ),
            ),
            cards=(
                StudyMaterialCardInput(
                    position=0,
                    front="What does the source explain?",
                    back="It explains the learning concept.",
                    explanation=None,
                    difficulty=None,
                    tags=(),
                    citation_keys=("P0001",),
                ),
            ),
            now=datetime.now(timezone.utc) + timedelta(days=1),
        )
        with ai_test_factory() as session:
            assert session.scalar(
                select(func.count()).select_from(StudyMaterial).where(
                    StudyMaterial.job_id == job_id
                )
            ) == 0
            assert session.scalar(
                select(func.count())
                .select_from(StudyMaterialCard)
                .join(
                    StudyMaterial,
                    StudyMaterial.id == StudyMaterialCard.material_id,
                )
                .where(StudyMaterial.job_id == job_id)
            ) == 0
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="stale flashcard lease",
        )


def test_study_material_job_leases_recover_and_reject_stale_tokens(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        document_worker_lease_seconds=30,
        document_retry_base_seconds=2,
        document_retry_max_seconds=8,
    )
    monkeypatch.setattr(study_material_jobs, "get_settings", lambda: settings)
    user_id = None
    queue_guard: Session | None = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "material-lifecycle")
            document, subject, space = _create_document(
                session, user=user, suffix="material-lifecycle"
            )
            generation = _create_ready_index(session, document)
            kit = StudyKit(
                user_id=user.id,
                subject_id=subject.id,
                study_space_id=space.id,
                title="Lifecycle kit",
            )
            session.add(kit)
            session.flush()
            jobs = [
                StudyMaterialJob(
                    user_id=user.id,
                    study_space_id=space.id,
                    study_kit_id=kit.id,
                    kind="notes",
                    status="queued",
                    attempts_used=0,
                    idempotency_key=f"lifecycle-{index}-{uuid4().hex}",
                    request_hash=str(index) * 64,
                    request_payload={},
                )
                for index in range(2)
            ]
            session.add_all(jobs)
            session.flush()
            for job in jobs:
                _add_material_source(
                    session,
                    user=user,
                    kit=kit,
                    job=job,
                    document=document,
                    generation=generation,
                )
            job_ids = {job.id for job in jobs}
            kit_id = kit.id
            document_id = document.id
            generation_id = generation.id
            user_id = user.id

    citation = StudyMaterialCitationInput(
        citation_key="P0001",
        document_id=document_id,
        generation_id=generation_id,
        chunk_index=0,
    )
    base_time = datetime.now(timezone.utc)
    try:
        queue_guard = ai_test_factory()
        queue_guard.begin()
        _lock_unrelated_study_material_jobs(queue_guard, job_ids)

        first_lease = claim_next_study_material_job(
            ai_test_factory, now=base_time
        )
        assert first_lease is not None
        assert renew_study_material_lease(
            ai_test_factory, first_lease, now=base_time + timedelta(seconds=1)
        )
        assert cancel_study_material_job(
            ai_test_factory,
            user_id=first_lease.user_id,
            job_id=first_lease.job_id,
            now=base_time + timedelta(seconds=2),
        )
        assert not renew_study_material_lease(
            ai_test_factory, first_lease, now=base_time + timedelta(seconds=3)
        )
        assert not finalize_study_material_job(
            ai_test_factory,
            first_lease,
            title="Late result",
            content={},
            citations=(citation,),
            now=base_time + timedelta(seconds=3),
        )

        second_lease = claim_next_study_material_job(
            ai_test_factory, now=base_time + timedelta(seconds=3)
        )
        assert second_lease is not None
        recovery_time = base_time + timedelta(seconds=34)
        assert not finalize_study_material_job(
            ai_test_factory,
            second_lease,
            title="Expired result",
            content={},
            citations=(citation,),
            now=recovery_time,
        )
        assert recover_expired_study_material_jobs(
            ai_test_factory, now=recovery_time
        ) == 1
        assert recover_expired_study_material_jobs(
            ai_test_factory, now=recovery_time
        ) == 0
        assert not fail_study_material_job(
            ai_test_factory,
            second_lease,
            error_code="stale_worker",
            retryable=True,
            now=recovery_time,
        )

        retried_lease = claim_next_study_material_job(
            ai_test_factory, now=base_time + timedelta(seconds=40)
        )
        assert retried_lease is not None
        assert retried_lease.job_id == second_lease.job_id
        assert retried_lease.token != second_lease.token
        assert fail_study_material_job(
            ai_test_factory,
            retried_lease,
            error_code="permanent_failure",
            retryable=False,
            now=base_time + timedelta(seconds=41),
        )
        with ai_test_factory() as session:
            cancelled = session.get(StudyMaterialJob, first_lease.job_id)
            failed = session.get(StudyMaterialJob, retried_lease.job_id)
            assert cancelled is not None and cancelled.status == "cancelled"
            assert cancelled.lease_token is None
            assert failed is not None and failed.status == "failed"
            assert failed.lease_token is None
            assert claim_next_study_material_job(
                ai_test_factory, now=base_time + timedelta(seconds=50)
            ) is None
            abandoned = StudyMaterialJob(
                user_id=user_id,
                study_space_id=failed.study_space_id,
                study_kit_id=kit_id,
                kind="notes",
                status="processing",
                attempts_used=1,
                idempotency_key=f"abandoned-{uuid4().hex}",
                request_hash="f" * 64,
                request_payload={},
            )
            session.add(abandoned)
            session.commit()
        assert recover_expired_study_material_jobs(
            ai_test_factory, now=base_time + timedelta(seconds=60)
        ) == 1
        assert recover_expired_study_material_jobs(
            ai_test_factory, now=base_time + timedelta(seconds=60)
        ) == 0
    finally:
        if queue_guard is not None:
            if queue_guard.in_transaction():
                queue_guard.rollback()
            queue_guard.close()
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study material lifecycle",
        )


@pytest.mark.parametrize("kind", ("notes", "flashcards"))
def test_study_material_cancellation_racing_finalization_is_serialized(
    ai_test_factory,
    kind: str,
) -> None:
    user_id = None
    engine = ai_test_factory.kw["bind"]
    lock_attempted = Event()
    backend_pid_lock = Lock()
    worker_operation = local()
    worker_operations_by_pid: dict[int, str] = {}
    listener_registered = False
    lock_holder: Session | None = None
    executor: ThreadPoolExecutor | None = None

    def observe_document_lock(
        _connection, cursor, statement, _parameters, _context, _many
    ) -> None:
        lowered = statement.lower()
        if (
            current_thread().name.startswith("ThreadPoolExecutor")
            and lowered.lstrip().startswith("select")
            and "from documents" in lowered
            and "for update" in lowered
        ):
            cursor.execute("SELECT pg_backend_pid()")
            backend_pid = int(cursor.fetchone()[0])
            cursor.execute("SET LOCAL lock_timeout = '15s'")
            with backend_pid_lock:
                worker_operations_by_pid[backend_pid] = worker_operation.name
                if len(worker_operations_by_pid) == 2:
                    lock_attempted.set()

    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "material-cancel-race")
                user_id = user.id
                document, subject, space = _create_document(
                    session, user=user, suffix="material-cancel-race"
                )
                generation = _create_ready_index(session, document)
                kit = StudyKit(
                    user_id=user.id,
                    subject_id=subject.id,
                    study_space_id=space.id,
                    title="Cancellation race kit",
                )
                session.add(kit)
                session.flush()
                lease_token = uuid4()
                job = StudyMaterialJob(
                    user_id=user.id,
                    study_space_id=space.id,
                    study_kit_id=kit.id,
                    kind=kind,
                    status="processing",
                    attempts_used=1,
                    lease_token=lease_token,
                    lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                    idempotency_key=f"cancel-race-{uuid4().hex}",
                    request_hash="d" * 64,
                    request_payload={"card_count": 1} if kind == "flashcards" else {},
                )
                session.add(job)
                session.flush()
                session.add(
                    StudyMaterialJobSource(
                        job_id=job.id,
                        document_id=document.id,
                        user_id=user.id,
                        study_space_id=space.id,
                        generation_id=generation.id,
                        source_document_sha256=document.content_sha256,
                    )
                )
                lease = StudyMaterialLease(
                    job_id=job.id,
                    user_id=user.id,
                    study_space_id=space.id,
                    study_kit_id=kit.id,
                    token=lease_token,
                    attempts_used=job.attempts_used,
                )
                job_id = job.id
                document_id = document.id

        def run_worker(operation: str, action: Callable[[], bool]) -> bool:
            worker_operation.name = operation
            try:
                return action()
            finally:
                del worker_operation.name

        def cancel() -> bool:
            return cancel_study_material_job(
                ai_test_factory, user_id=user_id, job_id=job_id
            )

        def finalize() -> bool:
            citation = StudyMaterialCitationInput(
                "source-1", document.id, generation.id, 0
            )
            if kind == "flashcards":
                return finalize_study_flashcard_job(
                    ai_test_factory,
                    lease,
                    title="Race result",
                    content={"card_count": 1},
                    citations=(citation,),
                    cards=(
                        StudyMaterialCardInput(
                            position=0,
                            front="What does the source explain?",
                            back="It explains the learning concept.",
                            explanation=None,
                            difficulty=None,
                            tags=(),
                            citation_keys=("source-1",),
                        ),
                    ),
                )
            return finalize_study_material_job(
                ai_test_factory,
                lease,
                title="Race result",
                content={"sections": []},
                citations=(citation,),
            )

        with ai_test_factory() as held_session:
            lock_holder = held_session
            lock_holder.begin()
            deletion_backend_pid = int(
                lock_holder.connection()
                .exec_driver_sql("SELECT pg_backend_pid()")
                .scalar_one()
            )
            lock_holder.scalar(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update()
            )
            event.listen(engine, "before_cursor_execute", observe_document_lock)
            listener_registered = True
            executor = ThreadPoolExecutor(max_workers=2)
            futures = []
            try:
                futures = [
                    executor.submit(run_worker, "cancellation", cancel),
                    executor.submit(run_worker, "finalization", finalize),
                ]
                synchronization_deadline = time.monotonic() + 5
                lock_attempted.wait(
                    timeout=max(0, synchronization_deadline - time.monotonic())
                )
                worker_backend_pids = set(worker_operations_by_pid)
                worker_pids = sorted(worker_backend_pids)
                expected_operations = {"cancellation", "finalization"}
                wait_diagnostics: dict[int, dict[str, object]] = {}
                while (
                    len(worker_backend_pids) == 2
                    and time.monotonic() < synchronization_deadline
                ):
                    wait_diagnostics = _postgres_lock_dependency_diagnostics(
                        ai_test_factory,
                        worker_backend_pids,
                        deletion_backend_pid,
                    )
                    if all(
                        diagnostic["blocked_on_expected_holder"]
                        for diagnostic in wait_diagnostics.values()
                    ):
                        break
                    time.sleep(0.01)

                failed_waits = {
                    worker_operations_by_pid.get(backend_pid, "unknown"): {
                        "backend_pid": backend_pid,
                        **wait_diagnostics.get(
                            backend_pid,
                            {
                                "blocked_on_expected_holder": False,
                                "expected_holder_pid": deletion_backend_pid,
                                "diagnostic": "No blocker-chain data was observed",
                            },
                        ),
                    }
                    for backend_pid in worker_pids
                    if not wait_diagnostics.get(backend_pid, {}).get(
                        "blocked_on_expected_holder", False
                    )
                }
                assert len(worker_backend_pids) == 2, (
                    "Expected two distinct worker backend PIDs; "
                    "observed worker PID/operation mapping "
                    f"{worker_operations_by_pid}, expected lock-holder PID "
                    f"{deletion_backend_pid}; wait details: {failed_waits}"
                )
                assert set(worker_operations_by_pid.values()) == expected_operations, (
                    "Expected one cancellation and one finalization worker; "
                    f"observed PID/operation mapping {worker_operations_by_pid}"
                )
                assert deletion_backend_pid not in worker_backend_pids
                assert not failed_waits, (
                    "Workers did not both wait through a PostgreSQL lock dependency "
                    "chain terminating at the test document-lock holder before "
                    f"timeout; wait details: {failed_waits}"
                )
                lock_holder.commit()
                cancel_result, finalize_result = [
                    future.result(timeout=12) for future in futures
                ]
            finally:
                if lock_holder.in_transaction():
                    lock_holder.rollback()
                lock_holder.close()
                lock_holder = None
                if executor is not None:
                    executor.shutdown(wait=True, cancel_futures=True)
                    executor = None
                if listener_registered:
                    event.remove(engine, "before_cursor_execute", observe_document_lock)
                    listener_registered = False
        assert not (cancel_result and finalize_result)
        with ai_test_factory() as session:
            job = session.get(StudyMaterialJob, job_id)
            assert job is not None
            if cancel_result:
                assert job.status == "cancelled"
                assert job.lease_token is None
                assert session.scalar(
                    select(func.count()).select_from(StudyMaterial).where(
                        StudyMaterial.job_id == job_id
                    )
                ) == 0
                assert session.scalar(
                    select(func.count()).select_from(StudyMaterialCard).where(
                        StudyMaterialCard.material_id.in_(
                            select(StudyMaterial.id).where(
                                StudyMaterial.job_id == job_id
                            )
                        )
                    )
                ) == 0
            else:
                assert finalize_result
                assert job.status == "completed"
                assert session.scalar(
                    select(func.count()).select_from(StudyMaterial).where(
                        StudyMaterial.job_id == job_id
                    )
                ) == 1
                if kind == "flashcards":
                    assert session.scalar(
                        select(func.count())
                        .select_from(StudyMaterialCard)
                        .join(
                            StudyMaterial,
                            StudyMaterial.id == StudyMaterialCard.material_id,
                        )
                        .where(StudyMaterial.job_id == job_id)
                    ) == 1
    finally:
        if lock_holder is not None:
            if lock_holder.in_transaction():
                lock_holder.rollback()
            lock_holder.close()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if listener_registered:
            event.remove(engine, "before_cursor_execute", observe_document_lock)
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study material cancellation race",
        )


def test_cancellation_refreshes_job_state_after_finalization_commits(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = None
    finalization_result: bool | None = None
    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "cancel-refresh-race")
                document, subject, space = _create_document(
                    session, user=user, suffix="cancel-refresh-race"
                )
                generation = _create_ready_index(session, document)
                kit = StudyKit(
                    user_id=user.id,
                    subject_id=subject.id,
                    study_space_id=space.id,
                    title="Cancellation refresh kit",
                )
                session.add(kit)
                session.flush()
                lease_token = uuid4()
                job = StudyMaterialJob(
                    user_id=user.id,
                    study_space_id=space.id,
                    study_kit_id=kit.id,
                    kind="notes",
                    status="processing",
                    attempts_used=1,
                    lease_token=lease_token,
                    lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                    idempotency_key=f"cancel-refresh-{uuid4().hex}",
                    request_hash="d" * 64,
                    request_payload={},
                )
                session.add(job)
                session.flush()
                session.add(
                    StudyMaterialJobSource(
                        job_id=job.id,
                        document_id=document.id,
                        user_id=user.id,
                        study_space_id=space.id,
                        generation_id=generation.id,
                        source_document_sha256=document.content_sha256,
                    )
                )
                lease = StudyMaterialLease(
                    job_id=job.id,
                    user_id=user.id,
                    study_space_id=space.id,
                    study_kit_id=kit.id,
                    token=lease_token,
                    attempts_used=job.attempts_used,
                )
                job_id = job.id
                document_id = document.id
                generation_id = generation.id
                user_id = user.id

        original_lock_source_documents = study_material_jobs._lock_source_documents

        def finalize_after_cancellation_snapshot(
            session: Session, source_document_ids: list[UUID]
        ) -> list[Document]:
            nonlocal finalization_result
            if finalization_result is None:
                finalization_result = False
                finalization_result = finalize_study_material_job(
                    ai_test_factory,
                    lease,
                    title="Completed before cancellation lock",
                    content={"sections": []},
                    citations=(
                        StudyMaterialCitationInput(
                            "P0001", document_id, generation_id, 0
                        ),
                    ),
                )
            return original_lock_source_documents(session, source_document_ids)

        monkeypatch.setattr(
            study_material_jobs,
            "_lock_source_documents",
            finalize_after_cancellation_snapshot,
        )
        cancel_result = cancel_study_material_job(
            ai_test_factory, user_id=user_id, job_id=job_id
        )

        assert finalization_result is True
        assert cancel_result is False
        with ai_test_factory() as session:
            job = session.get(StudyMaterialJob, job_id)
            assert job is not None and job.status == "completed"
            materials = list(
                session.scalars(
                    select(StudyMaterial).where(StudyMaterial.job_id == job_id)
                )
            )
            assert len(materials) == 1
            citations = list(
                session.scalars(
                    select(StudyMaterialCitation).where(
                        StudyMaterialCitation.job_id == job_id
                    )
                )
            )
            assert len(citations) == 1
            citation = citations[0]
            assert citation.study_material_id == materials[0].id
            assert citation.citation_key == "P0001"
            assert citation.document_id == document_id
            assert citation.generation_id == generation_id
            assert citation.chunk_index == 0
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study material cancellation refresh race",
        )


def test_source_deletion_racing_finalization_cleans_uncited_jobs_and_preserves_usage(
    ai_test_factory,
) -> None:
    user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "material-delete-race")
            document, subject, space = _create_document(
                session, user=user, suffix="material-delete-race"
            )
            generation = _create_ready_index(session, document)
            kit = StudyKit(
                user_id=user.id,
                subject_id=subject.id,
                study_space_id=space.id,
                title="Deletion race kit",
            )
            session.add(kit)
            session.flush()
            lease_token = uuid4()
            job = StudyMaterialJob(
                user_id=user.id,
                study_space_id=space.id,
                study_kit_id=kit.id,
                kind="notes",
                status="processing",
                attempts_used=1,
                lease_token=lease_token,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                idempotency_key=f"delete-race-{uuid4().hex}",
                request_hash="e" * 64,
                request_payload={},
            )
            session.add(job)
            session.flush()
            # The document is selected by this job but no citation has been written.
            session.add(
                StudyMaterialJobSource(
                    job_id=job.id,
                    document_id=document.id,
                    user_id=user.id,
                    study_space_id=space.id,
                    generation_id=generation.id,
                    source_document_sha256=document.content_sha256,
                )
            )
            reservation = AIUsageReservation(
                user_id=user.id,
                document_id=document.id,
                operation="study_notes",
                idempotency_key=f"delete-race-reservation-{uuid4().hex}",
                reserved_input_tokens=100,
                reserved_output_tokens=100,
                reserved_cost_usd=Decimal("0.001"),
                pricing_version="test-pricing-v1",
                model_name="test-model",
                profile_id=PROFILE_ID,
                expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
            )
            session.add(reservation)
            session.flush()
            usage_event = AIUsageEvent(
                user_id=user.id,
                document_id=document.id,
                reservation_id=reservation.id,
                operation="study_notes",
                model_name="test-model",
                profile_id=PROFILE_ID,
                input_tokens=100,
                output_tokens=50,
                estimated_cost_usd=Decimal("0.001"),
                pricing_version="test-pricing-v1",
                accounting_basis="conservative_estimate",
                idempotency_key=f"delete-race-event-{uuid4().hex}",
            )
            session.add(usage_event)
            session.flush()
            lease = StudyMaterialLease(
                job_id=job.id,
                user_id=user.id,
                study_space_id=space.id,
                study_kit_id=kit.id,
                token=lease_token,
                attempts_used=job.attempts_used,
            )
            document_id = document.id
            generation_id = generation.id
            job_id = job.id
            reservation_id = reservation.id
            usage_event_id = usage_event.id
            user_id = user.id

    barrier = Barrier(2)

    def finalize() -> bool:
        barrier.wait()
        return finalize_study_material_job(
            ai_test_factory,
            lease,
            title="Late result",
            content={"sections": []},
            citations=(
                StudyMaterialCitationInput("source-1", document_id, generation_id, 0),
            ),
        )

    def delete_source() -> None:
        barrier.wait()
        with ai_test_factory() as session:
            document = session.scalar(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update(of=Document)
            )
            assert delete_document_dependent_work(session, [document_id]) == {job_id}
            assert delete_document_dependent_work(session, [document_id]) == set()
            if document is not None:
                session.delete(document)
            session.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            finalize_future = executor.submit(finalize)
            delete_future = executor.submit(delete_source)
            finalize_result = finalize_future.result()
            delete_future.result()
        with ai_test_factory() as session:
            assert session.get(Document, document_id) is None
            assert session.get(StudyMaterialJob, job_id) is None
            assert session.scalar(
                select(func.count()).select_from(StudyMaterialJobSource).where(
                    StudyMaterialJobSource.job_id == job_id
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(StudyMaterial).where(
                    StudyMaterial.job_id == job_id
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(StudyMaterialCitation).where(
                    StudyMaterialCitation.job_id == job_id
                )
            ) == 0
            reservation = session.get(AIUsageReservation, reservation_id)
            usage_event = session.get(AIUsageEvent, usage_event_id)
            assert reservation is not None and reservation.document_id is None
            assert usage_event is not None and usage_event.document_id is None
            assert isinstance(finalize_result, bool)
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study material source deletion race",
        )


def test_index_activation_and_document_deletion_share_document_first_lock_order(
    ai_test_factory,
) -> None:
    user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "index-delete-lock-order")
            document, _, _ = _create_document(
                session, user=user, suffix="index-delete-lock-order"
            )
            generation = _create_ready_index(session, document)
            generation.status = "building"
            generation.ready_at = None
            token = uuid4()
            job = DocumentIndexJob(
                document_id=document.id,
                generation_id=generation.id,
                status="processing",
                attempts_used=1,
                lease_token=token,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
            )
            session.add(job)
            session.flush()
            lease = IndexLease(
                job_id=job.id,
                document_id=document.id,
                generation_id=generation.id,
                user_id=user.id,
                token=token,
                attempts_used=1,
            )
            document_id = document.id
            generation_id = generation.id
            job_id = job.id
            user_id = user.id

    barrier = Barrier(2)

    def activate() -> bool:
        barrier.wait()
        return _activate_generation(ai_test_factory, lease, expected_count=1)

    def delete_source() -> None:
        barrier.wait()
        with ai_test_factory() as session:
            document = session.scalar(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update(of=Document)
            )
            delete_document_dependent_work(session, [document_id])
            if document is not None:
                session.delete(document)
            session.commit()

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            activation_future = executor.submit(activate)
            deletion_future = executor.submit(delete_source)
            activation_result = activation_future.result()
            deletion_future.result()
        assert isinstance(activation_result, bool)
        with ai_test_factory() as session:
            assert session.get(Document, document_id) is None
            assert session.get(DocumentIndexGeneration, generation_id) is None
            assert session.get(DocumentIndexJob, job_id) is None
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="index activation and deletion lock order",
        )


def test_study_kit_deletion_preserves_sources_and_unrelated_kit(
    ai_test_factory,
) -> None:
    user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "kit-isolation")
            document, subject, space = _create_document(
                session, user=user, suffix="kit-isolation"
            )
            generation = _create_ready_index(session, document)
            first_kit = StudyKit(
                user_id=user.id,
                subject_id=subject.id,
                study_space_id=space.id,
                title="First kit",
            )
            second_kit = StudyKit(
                user_id=user.id,
                subject_id=subject.id,
                study_space_id=space.id,
                title="Second kit",
            )
            session.add_all([first_kit, second_kit])
            session.flush()
            jobs = [
                StudyMaterialJob(
                    user_id=user.id,
                    study_space_id=space.id,
                    study_kit_id=kit.id,
                    kind="notes",
                    status="queued",
                    attempts_used=0,
                    idempotency_key=f"kit-isolation-{index}-{uuid4().hex}",
                    request_hash=str(index) * 64,
                    request_payload={},
                )
                for index, kit in enumerate((first_kit, second_kit), start=1)
            ]
            session.add_all(jobs)
            session.flush()
            session.add_all(
                [
                    StudyMaterialJobSource(
                        job_id=job.id,
                        document_id=document.id,
                        user_id=user.id,
                        study_space_id=space.id,
                        generation_id=generation.id,
                        source_document_sha256=document.content_sha256,
                    )
                    for job in jobs
                ]
            )
            session.flush()
            first_kit_id = first_kit.id
            second_kit_id = second_kit.id
            first_job_id = jobs[0].id
            second_job_id = jobs[1].id
            document_id = document.id
            user_id = user.id

    try:
        with ai_test_factory() as session:
            delete_study_kit(
                session, user_id=user_id, study_kit_id=first_kit_id
            )
        with ai_test_factory() as session:
            assert session.get(Document, document_id) is not None
            assert session.get(StudyKit, first_kit_id) is None
            assert session.get(StudyMaterialJob, first_job_id) is None
            assert session.get(StudyKit, second_kit_id) is not None
            assert session.get(StudyMaterialJob, second_job_id) is not None
            assert session.scalar(
                select(func.count()).select_from(StudyMaterialJobSource).where(
                    StudyMaterialJobSource.job_id == second_job_id
                )
            ) == 1
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study kit deletion isolation",
        )


def test_parent_deletion_serializes_child_creation(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import study_spaces as study_spaces_service
    from app.services import subjects as subjects_service

    monkeypatch.setattr(study_spaces_service, "remove_storage_files", lambda _keys: False)
    monkeypatch.setattr(subjects_service, "remove_storage_files", lambda _keys: False)
    user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "parent-child-race")
            subject = Subject(user_id=user.id, name="Parent race subject")
            session.add(subject)
            session.flush()
            subject_id = subject.id
            user_id = user.id

    barrier = Barrier(2)

    def create_space() -> str:
        barrier.wait()
        with ai_test_factory() as session:
            try:
                create_study_space(
                    session,
                    user_id=user_id,
                    subject_id=subject_id,
                    payload=StudySpaceCreate(name="Concurrent space"),
                )
                return "created"
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                return "not_found"

    def delete_parent() -> None:
        barrier.wait()
        with ai_test_factory() as session:
            subjects_service.delete_subject(
                session, user_id=user_id, subject_id=subject_id
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            create_future = executor.submit(create_space)
            delete_future = executor.submit(delete_parent)
            assert create_future.result() in {"created", "not_found"}
            delete_future.result()
        with ai_test_factory() as session:
            assert session.get(Subject, subject_id) is None
            assert session.scalar(
                select(func.count()).select_from(StudySpace).where(
                    StudySpace.subject_id == subject_id
                )
            ) == 0
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="parent-child creation race",
        )


def test_study_space_deletion_serializes_kit_creation(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import study_spaces as study_spaces_service

    monkeypatch.setattr(study_spaces_service, "remove_storage_files", lambda _keys: False)
    user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "space-kit-race")
            _, subject, space = _create_document(
                session, user=user, suffix="space-kit-race"
            )
            user_id = user.id
            subject_id = subject.id
            space_id = space.id

    barrier = Barrier(2)

    def create_kit() -> str:
        barrier.wait()
        with ai_test_factory() as session:
            try:
                create_study_kit(
                    session,
                    user_id=user_id,
                    study_space_id=space_id,
                    payload=StudyKitCreate(title="Concurrent kit"),
                )
                return "created"
            except HTTPException as error:
                if error.status_code != 404:
                    raise
                return "not_found"

    def delete_space() -> None:
        barrier.wait()
        with ai_test_factory() as session:
            delete_study_space(
                session, user_id=user_id, study_space_id=space_id
            )

    try:
        with ThreadPoolExecutor(max_workers=2) as executor:
            create_future = executor.submit(create_kit)
            delete_future = executor.submit(delete_space)
            assert create_future.result() in {"created", "not_found"}
            delete_future.result()
        with ai_test_factory() as session:
            assert session.get(StudySpace, space_id) is None
            assert session.scalar(
                select(func.count()).select_from(StudyKit).where(
                    StudyKit.study_space_id == space_id
                )
            ) == 0
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study space and kit race",
        )


def test_subject_and_space_deletion_remove_selected_jobs(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import study_spaces as study_spaces_service
    from app.services import subjects as subjects_service

    monkeypatch.setattr(study_spaces_service, "remove_storage_files", lambda _keys: False)
    monkeypatch.setattr(subjects_service, "remove_storage_files", lambda _keys: False)
    user_id = None
    with ai_test_factory() as session:
        with session.begin():
            user = _create_user(session, "parent-job-cleanup")
            first_doc, first_subject, first_space = _create_document(
                session, user=user, suffix="space-job-cleanup"
            )
            first_generation = _create_ready_index(session, first_doc)
            second_doc, second_subject, second_space = _create_document(
                session, user=user, suffix="subject-job-cleanup"
            )
            second_generation = _create_ready_index(session, second_doc)
            kits = [
                StudyKit(
                    user_id=user.id,
                    subject_id=subject.id,
                    study_space_id=space.id,
                    title=f"Cleanup kit {index}",
                )
                for index, (subject, space) in enumerate(
                    ((first_subject, first_space), (second_subject, second_space)),
                    start=1,
                )
            ]
            session.add_all(kits)
            session.flush()
            jobs = [
                StudyMaterialJob(
                    user_id=user.id,
                    study_space_id=kit.study_space_id,
                    study_kit_id=kit.id,
                    kind="notes",
                    status="queued",
                    attempts_used=0,
                    idempotency_key=f"parent-cleanup-{index}-{uuid4().hex}",
                    request_hash=str(index) * 64,
                    request_payload={},
                )
                for index, kit in enumerate(kits, start=1)
            ]
            session.add_all(jobs)
            session.flush()
            session.add_all(
                [
                    StudyMaterialJobSource(
                        job_id=jobs[0].id,
                        document_id=first_doc.id,
                        user_id=user.id,
                        study_space_id=first_space.id,
                        generation_id=first_generation.id,
                        source_document_sha256=first_doc.content_sha256,
                    ),
                    StudyMaterialJobSource(
                        job_id=jobs[1].id,
                        document_id=second_doc.id,
                        user_id=user.id,
                        study_space_id=second_space.id,
                        generation_id=second_generation.id,
                        source_document_sha256=second_doc.content_sha256,
                    ),
                ]
            )
            first_job_id = jobs[0].id
            second_job_id = jobs[1].id
            first_space_id = first_space.id
            second_subject_id = second_subject.id
            first_doc_id = first_doc.id
            second_doc_id = second_doc.id
            user_id = user.id

    try:
        with ai_test_factory() as session:
            delete_study_space(
                session, user_id=user_id, study_space_id=first_space_id
            )
        with ai_test_factory() as session:
            delete_subject(
                session, user_id=user_id, subject_id=second_subject_id
            )
        with ai_test_factory() as session:
            assert session.get(StudyMaterialJob, first_job_id) is None
            assert session.get(StudyMaterialJob, second_job_id) is None
            assert session.get(Document, first_doc_id) is None
            assert session.get(Document, second_doc_id) is None
            assert session.scalar(
                select(func.count()).select_from(StudyMaterialJobSource).where(
                    StudyMaterialJobSource.job_id.in_(
                        (first_job_id, second_job_id)
                    )
                )
            ) == 0
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="parent job cleanup",
        )


def test_index_activation_locks_generation_before_job_and_revalidates_lease(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings()
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    statements: list[str] = []
    engine = ai_test_factory.kw["bind"]

    def capture_locks(_connection, _cursor, statement, _parameters, _context, _many):
        if "FOR UPDATE" in statement.upper():
            statements.append(statement.lower())

    user_id = None
    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "index-lock-order")
                document, _, _ = _create_document(
                    session, user=user, suffix="index-lock-order"
                )
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
                    status="building",
                )
                session.add(generation)
                session.flush()
                token = uuid4()
                job = DocumentIndexJob(
                    document_id=document.id,
                    generation_id=generation.id,
                    status="processing",
                    attempts_used=1,
                    lease_token=token,
                    lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                )
                session.add(job)
                session.flush()
                lease = IndexLease(
                    job_id=job.id,
                    document_id=document.id,
                    generation_id=generation.id,
                    user_id=user.id,
                    token=token,
                    attempts_used=1,
                )
                document_id = document.id
                user_id = user.id

        event.listen(engine, "before_cursor_execute", capture_locks)
        assert _activate_generation(ai_test_factory, lease, expected_count=0)
        assert not _activate_generation(ai_test_factory, lease, expected_count=0)
        lock_tables = [
            "document_index_generations" if "document_index_generations" in sql
            else "document_index_jobs" if "document_index_jobs" in sql
            else "other"
            for sql in statements
        ]
        generation_lock = lock_tables.index("document_index_generations")
        job_lock = lock_tables.index("document_index_jobs")
        assert generation_lock < job_lock
        with ai_test_factory() as session:
            job = session.scalar(select(DocumentIndexJob).where(DocumentIndexJob.id == lease.job_id))
            generation = session.get(DocumentIndexGeneration, lease.generation_id)
            assert job is not None and job.status == "completed"
            assert generation is not None and generation.status == "ready"

        with ai_test_factory() as session:
            with session.begin():
                owner = session.get(User, user_id)
                assert owner is not None
                failure_document, _, _ = _create_document(
                    session, user=owner, suffix="index-lock-order-failure"
                )
                generation = DocumentIndexGeneration(
                    document_id=failure_document.id,
                    generation_number=1,
                    source_document_sha256=failure_document.content_sha256,
                    embedding_profile_id=PROFILE_ID,
                    model_name="text-embedding-3-small",
                    dimensions=1536,
                    distance_metric="cosine",
                    chunking_version="page-aware-utf8-byte-overlap-v1",
                    token_count_strategy="utf8-byte-upper-bound-v1",
                    status="building",
                )
                session.add(generation)
                session.flush()
                token = uuid4()
                job = DocumentIndexJob(
                    document_id=failure_document.id,
                    generation_id=generation.id,
                    status="processing",
                    attempts_used=1,
                    lease_token=token,
                    lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
                )
                session.add(job)
                session.flush()
                failure_lease = IndexLease(
                    job_id=job.id,
                    document_id=failure_document.id,
                    generation_id=generation.id,
                    user_id=failure_document.user_id,
                    token=token,
                    attempts_used=1,
                )
                failure_generation_id = generation.id
                failure_job_id = job.id

        statements.clear()
        _record_index_failure(
            ai_test_factory, failure_lease, "test_failure", retryable=False
        )
        failure_lock_tables = [
            "document_index_generations" if "document_index_generations" in sql
            else "document_index_jobs" if "document_index_jobs" in sql
            else "other"
            for sql in statements
        ]
        assert failure_lock_tables.index("document_index_generations") < (
            failure_lock_tables.index("document_index_jobs")
        )
        _record_index_failure(
            ai_test_factory, failure_lease, "late_failure", retryable=False
        )
        with ai_test_factory() as session:
            failure_job = session.get(DocumentIndexJob, failure_job_id)
            failure_generation = session.get(
                DocumentIndexGeneration, failure_generation_id
            )
            assert failure_job is not None and failure_job.error_code == "test_failure"
            assert failure_generation is not None and failure_generation.status == "failed"
    finally:
        event.remove(engine, "before_cursor_execute", capture_locks)
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="index lock order",
        )


def test_study_material_idempotency_includes_kit_and_payload(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(study_material_jobs, "get_settings", lambda: _settings())
    user_id = None
    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "material-idempotency")
                document, subject, space = _create_document(
                    session, user=user, suffix="material-idempotency"
                )
                generation = _create_ready_index(session, document)
                kits = [
                    StudyKit(
                        user_id=user.id,
                        subject_id=subject.id,
                        study_space_id=space.id,
                        title=f"Idempotency kit {index}",
                    )
                    for index in (1, 2)
                ]
                session.add_all(kits)
                session.flush()
                user_id = user.id
                kit_ids = [kit.id for kit in kits]
                document_id = document.id
                generation_id = generation.id

        source = StudyMaterialSource(document_id=document_id, generation_id=generation_id)
        with ai_test_factory() as session:
            original = enqueue_study_material_job(
                session,
                user_id=user_id,
                study_kit_id=kit_ids[0],
                idempotency_key="same-kit-replay",
                request_payload={"goal": "review"},
                sources=[source],
            )
            original_id = original.id
            session.commit()
        with ai_test_factory() as session:
            replay = enqueue_study_material_job(
                session,
                user_id=user_id,
                study_kit_id=kit_ids[0],
                idempotency_key="same-kit-replay",
                request_payload={"goal": "review"},
                sources=[source],
            )
            assert replay.id == original_id
            session.rollback()
        with ai_test_factory() as session:
            with pytest.raises(IdempotencyConflict):
                enqueue_study_material_job(
                    session,
                    user_id=user_id,
                    study_kit_id=kit_ids[1],
                    idempotency_key="same-kit-replay",
                    request_payload={"goal": "review"},
                    sources=[source],
                )
            session.rollback()

        with ai_test_factory() as session:
            enqueue_study_material_job(
                session,
                user_id=user_id,
                study_kit_id=kit_ids[0],
                idempotency_key="different-payload",
                request_payload={"goal": "review"},
                sources=[source],
            )
            session.commit()
        with ai_test_factory() as session:
            with pytest.raises(IdempotencyConflict):
                enqueue_study_material_job(
                    session,
                    user_id=user_id,
                    study_kit_id=kit_ids[0],
                    idempotency_key="different-payload",
                    request_payload={"goal": "exam preparation"},
                    sources=[source],
                )
            session.rollback()
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study material idempotency",
        )


def test_expired_study_material_jobs_cannot_bypass_retry_schedule(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(
        document_worker_lease_seconds=30,
        document_retry_base_seconds=10,
        document_retry_max_seconds=40,
    )
    monkeypatch.setattr(study_material_jobs, "get_settings", lambda: settings)
    user_id = None
    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "material-backoff")
                document, subject, space = _create_document(
                    session, user=user, suffix="material-backoff"
                )
                kit = StudyKit(
                    user_id=user.id,
                    subject_id=subject.id,
                    study_space_id=space.id,
                    title="Backoff kit",
                )
                session.add(kit)
                session.flush()
                expired_at = datetime.now(timezone.utc) - timedelta(seconds=1)
                retrying = _new_material_job(
                    session,
                    user=user,
                    kit=kit,
                    suffix="retrying",
                    status="processing",
                    attempts_used=1,
                    lease_token=uuid4(),
                    lease_expires_at=expired_at,
                )
                exhausted = _new_material_job(
                    session,
                    user=user,
                    kit=kit,
                    suffix="exhausted",
                    status="processing",
                    attempts_used=5,
                    lease_token=uuid4(),
                    lease_expires_at=expired_at,
                )
                retrying_id = retrying.id
                exhausted_id = exhausted.id
                user_id = user.id

        recovery_time = datetime.now(timezone.utc)
        with ai_test_factory() as queue_guard:
            queue_guard.begin()
            _lock_unrelated_study_material_jobs(
                queue_guard, {retrying_id, exhausted_id}
            )
            returned_job_ids: list[UUID | None] = []
            lease_before_recovery = claim_next_study_material_job(
                ai_test_factory, now=recovery_time
            )
            returned_job_ids.append(
                lease_before_recovery.job_id if lease_before_recovery else None
            )
            assert lease_before_recovery is None
            assert recover_expired_study_material_jobs(
                ai_test_factory, now=recovery_time
            ) == 2
            before_backoff = recovery_time + timedelta(seconds=9)
            lease_before_backoff = claim_next_study_material_job(
                ai_test_factory, now=before_backoff
            )
            returned_job_ids.append(
                lease_before_backoff.job_id if lease_before_backoff else None
            )
            assert lease_before_backoff is None
            with ai_test_factory() as session:
                retrying = session.get(StudyMaterialJob, retrying_id)
                exhausted = session.get(StudyMaterialJob, exhausted_id)
                assert retrying is not None
                assert retrying.status == "queued"
                assert retrying.next_attempt_at == recovery_time + timedelta(seconds=10)
                assert exhausted is not None
                assert exhausted.status == "failed"
                assert exhausted.error_code == "attempts_exhausted"
            lease = claim_next_study_material_job(
                ai_test_factory, now=recovery_time + timedelta(seconds=10)
            )
            returned_job_ids.append(lease.job_id if lease else None)
            assert lease is not None and lease.job_id == retrying_id
            assert returned_job_ids == [None, None, retrying_id]
            assert lease.attempts_used == 2
            queue_guard.rollback()
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study material backoff",
        )


@pytest.mark.parametrize(
    "intervening_state",
    ["normal", "ready", "requeued", "unexpected_lease", "repeated"],
)
def test_exhausted_index_generation_followup_revalidates_current_state(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
    intervening_state: str,
) -> None:
    settings = _settings(ai_enabled=True)
    monkeypatch.setattr(document_indexing, "get_settings", lambda: settings)
    original_followup = document_indexing._mark_exhausted_generation_failed
    user_id = None
    expected: dict[str, UUID] = {}

    def followup_with_intervening_state(session_factory, **kwargs) -> None:
        expected.update(kwargs)
        if intervening_state == "ready":
            with session_factory() as session:
                generation = session.get(
                    DocumentIndexGeneration, kwargs["generation_id"]
                )
                assert generation is not None
                generation.status = "ready"
                generation.ready_at = datetime.now(timezone.utc)
                session.commit()
        elif intervening_state == "requeued":
            with session_factory() as session:
                document = session.get(Document, kwargs["document_id"])
                assert document is not None
                document_indexing.ensure_index_generation(
                    session, document, settings=settings
                )
                session.commit()
        elif intervening_state == "unexpected_lease":
            with session_factory() as session:
                job = session.get(DocumentIndexJob, kwargs["job_id"])
                assert job is not None
                job.lease_token = uuid4()
                job.lease_expires_at = datetime.now(timezone.utc) + timedelta(minutes=1)
                session.commit()
        original_followup(session_factory, **kwargs)
        if intervening_state == "repeated":
            original_followup(session_factory, **kwargs)

    monkeypatch.setattr(
        document_indexing,
        "_mark_exhausted_generation_failed",
        followup_with_intervening_state,
    )
    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, f"index-exhausted-{intervening_state}")
                document, generation, job = _create_exhausted_index_job(
                    session, user=user, suffix=f"index-exhausted-{intervening_state}"
                )
                expected.update(
                    document_id=document.id,
                    generation_id=generation.id,
                    job_id=job.id,
                )
                user_id = user.id

        with ai_test_factory() as queue_guard:
            queue_guard.begin()
            queue_guard.execute(text("SET LOCAL lock_timeout = '8s'"))
            queue_guard.scalars(
                select(DocumentIndexJob)
                .where(DocumentIndexJob.id != expected["job_id"])
                .order_by(DocumentIndexJob.id)
                .with_for_update()
            ).all()
            claim_result = _claim_index_job(ai_test_factory)
            returned_job_ids = [
                claim_result.job_id if claim_result is not None else None
            ]
            assert returned_job_ids == [None]
            queue_guard.rollback()
        with ai_test_factory() as session:
            document = session.get(Document, expected["document_id"])
            generation = session.get(
                DocumentIndexGeneration, expected["generation_id"]
            )
            job = session.get(DocumentIndexJob, expected["job_id"])
            assert document is not None
            assert generation is not None and job is not None
            if intervening_state in {"normal", "repeated"}:
                assert job.status == "failed"
                assert job.attempts_used == 5
                assert job.lease_token is None
                assert job.lease_expires_at is None
                assert job.next_attempt_at is None
                assert job.completed_at is not None
                assert generation.status == "failed"
                assert generation.error_code == "attempts_exhausted"
            elif intervening_state == "ready":
                assert generation.status == "ready"
                assert job.status == "failed"
            elif intervening_state == "requeued":
                assert generation.status == "building"
                assert job.status == "queued"
                assert job.attempts_used == 0
                assert job.lease_token is None
                assert job.lease_expires_at is None
            else:
                assert generation.status == "building"
                assert job.status == "failed"
                assert job.lease_token is not None
                assert job.lease_expires_at is not None
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description=f"exhausted index follow-up {intervening_state}",
        )


def test_concurrent_study_material_claims_skip_locked_job(
    ai_test_factory,
) -> None:
    user_id = None
    engine = ai_test_factory.kw["bind"]
    reached_claim_select = Barrier(2)
    pid_lock = Lock()
    claim_backend_pids: set[int] = set()
    queue_guard: Session | None = None
    first_job_lock: Session | None = None
    listener_registered = False

    def synchronize_claim_query(
        _connection, cursor, statement, _parameters, _context, _many
    ):
        lowered = statement.lower()
        if (
            lowered.lstrip().startswith("select")
            and "from study_material_jobs" in lowered
            and "join study_kits" in lowered
            and "for update" in lowered
        ):
            cursor.execute("SELECT pg_backend_pid()")
            backend_pid = int(cursor.fetchone()[0])
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            with pid_lock:
                claim_backend_pids.add(backend_pid)
            reached_claim_select.wait(timeout=5)

    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "material-claim-race")
                _, subject, space = _create_document(
                    session, user=user, suffix="material-claim-race"
                )
                kit = StudyKit(
                    user_id=user.id,
                    subject_id=subject.id,
                    study_space_id=space.id,
                    title="Claim race kit",
                )
                session.add(kit)
                session.flush()
                first = _new_material_job(
                    session, user=user, kit=kit, suffix="claim-first"
                )
                second = _new_material_job(
                    session, user=user, kit=kit, suffix="claim-second"
                )
                first.created_at = datetime.now(timezone.utc) - timedelta(minutes=1)
                second.created_at = datetime.now(timezone.utc)
                first_id, second_id = first.id, second.id
                user_id = user.id

        queue_guard = ai_test_factory()
        queue_guard.begin()
        _lock_unrelated_study_material_jobs(queue_guard, {first_id, second_id})

        first_job_lock = ai_test_factory()
        first_job_lock.begin()
        first_job_lock.scalar(
            select(StudyMaterialJob)
            .where(StudyMaterialJob.id == first_id)
            .with_for_update()
        )

        event.listen(engine, "before_cursor_execute", synchronize_claim_query)
        listener_registered = True
        try:
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [
                    pool.submit(claim_next_study_material_job, ai_test_factory)
                    for _ in range(2)
                ]
                results = [future.result(timeout=10) for future in futures]
        finally:
            event.remove(engine, "before_cursor_execute", synchronize_claim_query)
            listener_registered = False

        claimed_ids = [lease.job_id for lease in results if lease is not None]
        returned_job_ids = [
            lease.job_id if lease is not None else None for lease in results
        ]
        assert claim_backend_pids and len(claim_backend_pids) == 2
        assert sorted(
            (job_id for job_id in returned_job_ids if job_id is not None),
            key=str,
        ) == [second_id]
        assert returned_job_ids.count(None) == 1
        assert len(claimed_ids) == 1 and claimed_ids[0] == second_id
        assert len(
            {lease.token for lease in results if lease is not None}
        ) == len(claimed_ids)
        with ai_test_factory() as session:
            first = session.get(StudyMaterialJob, first_id)
            second = session.get(StudyMaterialJob, second_id)
            assert first is not None and first.status == "queued"
            assert second is not None and second.status == "processing"
            assert second.lease_token == next(
                lease.token for lease in results if lease is not None
            )
        first_job_lock.rollback()
        first_job_lock.close()
        first_job_lock = None
        first_lease = claim_next_study_material_job(ai_test_factory)
        assert first_lease is not None and first_lease.job_id == first_id
        assert first_lease.job_id not in claimed_ids
    finally:
        if listener_registered:
            event.remove(engine, "before_cursor_execute", synchronize_claim_query)
        if first_job_lock is not None:
            if first_job_lock.in_transaction():
                first_job_lock.rollback()
            first_job_lock.close()
        if queue_guard is not None:
            if queue_guard.in_transaction():
                queue_guard.rollback()
            queue_guard.close()
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study material claim race",
        )


def test_expired_job_recovery_and_claim_are_disjoint(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _settings(document_retry_base_seconds=5, document_retry_max_seconds=30)
    monkeypatch.setattr(study_material_jobs, "get_settings", lambda: settings)
    user_id = None
    engine = ai_test_factory.kw["bind"]
    recovery_update_reached = Event()
    claim_select_reached = Event()
    release_recovery = Event()
    listener_registered = False
    queue_guard: Session | None = None
    executor: ThreadPoolExecutor | None = None
    claim_result_job_ids: list[UUID | None] = []

    def synchronize_recovery_and_claim(
        _connection, cursor, statement, _parameters, _context, _many
    ):
        lowered = statement.lower()
        if lowered.lstrip().startswith("update study_material_jobs"):
            recovery_update_reached.set()
            if not release_recovery.wait(timeout=10):
                raise TimeoutError("Recovery test was not released")
        elif (
            lowered.lstrip().startswith("select")
            and "from study_material_jobs" in lowered
            and "join study_kits" in lowered
            and "for update" in lowered
        ):
            cursor.execute("SET LOCAL lock_timeout = '5s'")
            claim_select_reached.set()

    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "material-recovery-claim")
                _, subject, space = _create_document(
                    session, user=user, suffix="material-recovery-claim"
                )
                kit = StudyKit(
                    user_id=user.id,
                    subject_id=subject.id,
                    study_space_id=space.id,
                    title="Recovery claim kit",
                )
                session.add(kit)
                session.flush()
                job = _new_material_job(
                    session,
                    user=user,
                    kit=kit,
                    suffix="expired",
                    status="processing",
                    attempts_used=1,
                    lease_token=uuid4(),
                    lease_expires_at=datetime.now(timezone.utc) - timedelta(seconds=1),
                )
                job_id = job.id
                user_id = user.id

        recovery_time = datetime.now(timezone.utc)
        queue_guard = ai_test_factory()
        queue_guard.begin()
        _lock_unrelated_study_material_jobs(queue_guard, {job_id})

        event.listen(engine, "before_cursor_execute", synchronize_recovery_and_claim)
        listener_registered = True
        try:
            executor = ThreadPoolExecutor(max_workers=2)
            recovery_future = executor.submit(
                    recover_expired_study_material_jobs,
                    ai_test_factory,
                    now=recovery_time,
                )
            assert recovery_update_reached.wait(timeout=5)
            claim_future = executor.submit(
                claim_next_study_material_job,
                ai_test_factory,
                now=recovery_time,
            )
            try:
                assert claim_select_reached.wait(timeout=5)
                concurrent_claim = claim_future.result(timeout=5)
                claim_result_job_ids.append(
                    concurrent_claim.job_id if concurrent_claim is not None else None
                )
                assert concurrent_claim is None
            finally:
                release_recovery.set()
            recovered_count = recovery_future.result(timeout=10)
            assert recovered_count == 1
        finally:
            release_recovery.set()
            if executor is not None:
                executor.shutdown(wait=True, cancel_futures=True)
                executor = None
            if listener_registered:
                event.remove(
                    engine, "before_cursor_execute", synchronize_recovery_and_claim
                )
                listener_registered = False

        with ai_test_factory() as session:
            recovered = session.get(StudyMaterialJob, job_id)
            assert recovered is not None
            assert recovered.status == "queued"
            assert recovered.attempts_used == 1
            assert recovered.next_attempt_at == recovery_time + timedelta(seconds=5)
            assert recovered.lease_token is None
            assert recovered.lease_expires_at is None
        lease_before_retry = claim_next_study_material_job(
            ai_test_factory, now=recovery_time + timedelta(seconds=4)
        )
        claim_result_job_ids.append(
            lease_before_retry.job_id if lease_before_retry is not None else None
        )
        assert lease_before_retry is None
        lease = claim_next_study_material_job(
            ai_test_factory, now=recovery_time + timedelta(seconds=5)
        )
        claim_result_job_ids.append(lease.job_id if lease is not None else None)
        assert lease is not None and lease.job_id == job_id
        assert claim_result_job_ids == [None, None, job_id]
        assert lease.attempts_used == 2
        with ai_test_factory() as session:
            claimed = session.get(StudyMaterialJob, job_id)
            assert claimed is not None
            assert claimed.status == "processing"
            assert claimed.lease_token == lease.token
            assert claimed.attempts_used == 2
    finally:
        release_recovery.set()
        if executor is not None:
            executor.shutdown(wait=True, cancel_futures=True)
        if listener_registered:
            event.remove(engine, "before_cursor_execute", synchronize_recovery_and_claim)
        if queue_guard is not None:
            if queue_guard.in_transaction():
                queue_guard.rollback()
            queue_guard.close()
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study material recovery claim",
        )


def test_enqueue_waiting_on_document_deletion_cannot_leave_orphan_source(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import documents as documents_service

    monkeypatch.setattr(study_material_jobs, "get_settings", lambda: _settings())
    monkeypatch.setattr(documents_service, "remove_storage_file", lambda _key: None)
    user_id = None
    source_lock_attempted = Event()
    enqueue_backend_pid: list[int] = []
    engine = ai_test_factory.kw["bind"]
    listener_registered = False
    deleting_session: Session | None = None

    def observe_source_lock(_connection, cursor, statement, _parameters, _context, _many):
        lowered = statement.lower()
        if (
            current_thread().name.startswith("ThreadPoolExecutor")
            and lowered.lstrip().startswith("select")
            and "from documents" in lowered
            and "for update" in lowered
        ):
            cursor.execute("SELECT pg_backend_pid()")
            enqueue_backend_pid.append(int(cursor.fetchone()[0]))
            cursor.execute("SET LOCAL lock_timeout = '8s'")
            source_lock_attempted.set()

    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "enqueue-delete-race")
                document, subject, space = _create_document(
                    session, user=user, suffix="enqueue-delete-race"
                )
                generation = _create_ready_index(session, document)
                kit = StudyKit(
                    user_id=user.id,
                    subject_id=subject.id,
                    study_space_id=space.id,
                    title="Enqueue deletion kit",
                )
                session.add(kit)
                session.flush()
                document_id, generation_id, kit_id = document.id, generation.id, kit.id
                user_id = user.id

        with ai_test_factory() as deleting_session:
            deleting_session.begin()
            deletion_backend_pid = int(
                deleting_session.connection()
                .exec_driver_sql("SELECT pg_backend_pid()")
                .scalar_one()
            )
            deleting_session.scalar(
                select(Document)
                .where(Document.id == document_id)
                .with_for_update()
            )
            assert deletion_backend_pid > 0
            event.listen(engine, "before_cursor_execute", observe_source_lock)
            listener_registered = True

            def enqueue() -> str:
                try:
                    with ai_test_factory() as session:
                        enqueue_study_material_job(
                            session,
                            user_id=user_id,
                            study_kit_id=kit_id,
                            idempotency_key="enqueue-delete-race",
                            request_payload={"goal": "review"},
                            sources=[
                                StudyMaterialSource(
                                    document_id=document_id,
                                    generation_id=generation_id,
                                )
                            ],
                        )
                        session.commit()
                    return "created"
                except ValueError:
                    return "rejected"

            with ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(enqueue)
                assert source_lock_attempted.wait(timeout=5)
                assert len(enqueue_backend_pid) == 1
                assert enqueue_backend_pid[0] != deletion_backend_pid
                assert _wait_for_postgres_lock_wait(
                    ai_test_factory,
                    enqueue_backend_pid[0],
                    deletion_backend_pid,
                    timeout=5,
                )
                assert not future.done()
                delete_document(
                    deleting_session,
                    user_id=user_id,
                    document_id=document_id,
                )
                assert future.result(timeout=12) == "rejected"

        with ai_test_factory() as session:
            assert session.get(Document, document_id) is None
            assert session.scalar(
                select(func.count()).select_from(StudyMaterialJob).where(
                    StudyMaterialJob.idempotency_key == "enqueue-delete-race"
                )
            ) == 0
            assert session.scalar(
                select(func.count()).select_from(StudyMaterialJobSource).where(
                    StudyMaterialJobSource.document_id == document_id
                )
            ) == 0
    finally:
        if deleting_session is not None and deleting_session.in_transaction():
            deleting_session.rollback()
        if listener_registered:
            event.remove(engine, "before_cursor_execute", observe_source_lock)
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="enqueue document deletion race",
        )


def test_concurrent_finalization_allocates_distinct_material_versions(
    ai_test_factory,
) -> None:
    user_id = None
    worker_start_barrier = Barrier(2)
    citation: StudyMaterialCitationInput

    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "material-finalize-race")
                document, subject, space = _create_document(
                    session, user=user, suffix="material-finalize-race"
                )
                generation = _create_ready_index(session, document)
                kit = StudyKit(
                    user_id=user.id,
                    subject_id=subject.id,
                    study_space_id=space.id,
                    title="Finalization race kit",
                )
                session.add(kit)
                session.flush()
                now = datetime.now(timezone.utc)
                leases: list[StudyMaterialLease] = []
                for index in range(2):
                    token = uuid4()
                    job = _new_material_job(
                        session,
                        user=user,
                        kit=kit,
                        suffix=f"finalize-{index}",
                        status="processing",
                        attempts_used=1,
                        lease_token=token,
                        lease_expires_at=now + timedelta(minutes=5),
                    )
                    _add_material_source(
                        session,
                        user=user,
                        kit=kit,
                        job=job,
                        document=document,
                        generation=generation,
                    )
                    leases.append(
                        StudyMaterialLease(
                            job_id=job.id,
                            user_id=user.id,
                            study_space_id=space.id,
                            study_kit_id=kit.id,
                            token=token,
                            attempts_used=1,
                        )
                    )
                user_id = user.id
                document_id = document.id
                generation_id = generation.id

        citation = StudyMaterialCitationInput(
            citation_key="P0001",
            document_id=document_id,
            generation_id=generation_id,
            chunk_index=0,
        )

        def finalize(lease: StudyMaterialLease) -> bool:
            return finalize_study_material_job(
                ai_test_factory,
                lease,
                title="Concurrent notes",
                content={"sections": []},
                citations=(citation,),
            )

        def finalize_concurrently(lease: StudyMaterialLease) -> bool:
            worker_start_barrier.wait(timeout=5)
            return finalize(lease)

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [
                future.result(timeout=15)
                for future in [
                    pool.submit(finalize_concurrently, lease) for lease in leases
                ]
            ]
        assert results == [True, True]
        with ai_test_factory() as session:
            versions = list(
                session.scalars(
                    select(StudyMaterial.version_number)
                    .where(StudyMaterial.study_kit_id == leases[0].study_kit_id)
                    .order_by(StudyMaterial.version_number)
                )
            )
            assert versions == [1, 2]
            material_ids_by_job = dict(
                session.execute(
                    select(StudyMaterial.job_id, StudyMaterial.id).where(
                        StudyMaterial.job_id.in_([lease.job_id for lease in leases])
                    )
                ).all()
            )
            citations = session.execute(
                select(
                    StudyMaterialCitation.job_id,
                    StudyMaterialCitation.study_material_id,
                    StudyMaterialCitation.citation_key,
                    StudyMaterialCitation.document_id,
                    StudyMaterialCitation.generation_id,
                    StudyMaterialCitation.chunk_index,
                )
                .where(
                    StudyMaterialCitation.job_id.in_(
                        [lease.job_id for lease in leases]
                    )
                )
                .order_by(StudyMaterialCitation.job_id)
            ).all()
            assert len(citations) == 2
            assert all(
                citation.study_material_id == material_ids_by_job[citation.job_id]
                and citation.citation_key == "P0001"
                and citation.document_id == document_id
                and citation.generation_id == generation_id
                and citation.chunk_index == 0
                for citation in citations
            )
        assert not finalize(leases[0])
        with ai_test_factory() as session:
            assert session.scalar(
                select(func.count()).select_from(StudyMaterial).where(
                    StudyMaterial.study_kit_id == leases[0].study_kit_id
                )
            ) == 2
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="study material finalization race",
        )


def test_parent_deletion_cleans_generated_dependencies_but_preserves_unrelated_data(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.services import documents as documents_service
    from app.services import study_spaces as study_spaces_service
    from app.services import subjects as subjects_service

    monkeypatch.setattr(documents_service, "remove_storage_file", lambda _key: None)
    monkeypatch.setattr(study_spaces_service, "remove_storage_files", lambda _keys: False)
    monkeypatch.setattr(subjects_service, "remove_storage_files", lambda _keys: False)
    user_id = None
    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "generated-parent-cleanup")
                cases = [
                    _create_document(session, user=user, suffix=f"generated-{index}")
                    for index in range(4)
                ]
                documents = [case[0] for case in cases]
                subjects = [case[1] for case in cases]
                spaces = [case[2] for case in cases]
                generations = [
                    _create_ready_index(session, document) for document in documents
                ]
                kits = [
                    StudyKit(
                        user_id=user.id,
                        subject_id=subjects[index].id,
                        study_space_id=spaces[index].id,
                        title=f"Generated kit {index}",
                    )
                    for index in range(4)
                ]
                session.add_all(kits)
                session.flush()
                jobs = [
                    _new_material_job(
                        session, user=user, kit=kits[index], suffix=f"parent-job-{index}"
                    )
                    for index in range(4)
                ]
                materials = [
                    StudyMaterial(
                        user_id=user.id,
                        study_kit_id=kits[index].id,
                        job_id=jobs[index].id,
                        kind="notes",
                        version_number=1,
                        title=f"Generated material {index}",
                        content={"sections": []},
                    )
                    for index in range(4)
                ]
                session.add_all(materials)
                session.flush()
                for index in range(4):
                    _add_material_source(
                        session,
                        user=user,
                        kit=kits[index],
                        job=jobs[index],
                        document=documents[index],
                        generation=generations[index],
                    )
                citations = [
                    StudyMaterialCitation(
                        study_material_id=materials[index].id,
                        job_id=jobs[index].id,
                        citation_key="source-1",
                        document_id=documents[index].id,
                        generation_id=generations[index].id,
                        chunk_index=0,
                    )
                    for index in range(4)
                ]
                session.add_all(citations)
                session.flush()
                deleted_ids = [documents[index].id for index in range(3)]
                deleted_job_ids = [jobs[index].id for index in range(3)]
                deleted_material_ids = [materials[index].id for index in range(3)]
                deleted_citation_ids = [citations[index].id for index in range(3)]
                document_id = documents[0].id
                space_id = spaces[1].id
                subject_id = subjects[2].id
                unrelated_document_id = documents[3].id
                unrelated_kit_id = kits[3].id
                unrelated_job_id = jobs[3].id
                unrelated_material_id = materials[3].id
                unrelated_citation_id = citations[3].id
                user_id = user.id

        with ai_test_factory() as session:
            delete_document(session, user_id=user_id, document_id=document_id)
        with ai_test_factory() as session:
            delete_study_space(session, user_id=user_id, study_space_id=space_id)
        with ai_test_factory() as session:
            delete_subject(session, user_id=user_id, subject_id=subject_id)
        with ai_test_factory() as session:
            for identifier in deleted_ids:
                assert session.get(Document, identifier) is None
            for identifier in deleted_job_ids:
                assert session.get(StudyMaterialJob, identifier) is None
            for identifier in deleted_material_ids:
                assert session.get(StudyMaterial, identifier) is None
            for identifier in deleted_citation_ids:
                assert session.get(StudyMaterialCitation, identifier) is None
            assert session.get(Document, unrelated_document_id) is not None
            assert session.get(StudyKit, unrelated_kit_id) is not None
            assert session.get(StudyMaterialJob, unrelated_job_id) is not None
            assert session.get(StudyMaterial, unrelated_material_id) is not None
            assert session.get(StudyMaterialCitation, unrelated_citation_id) is not None
            assert session.scalar(
                select(func.count()).select_from(StudyMaterialJobSource).where(
                    StudyMaterialJobSource.document_id.in_(deleted_ids)
                )
            ) == 0
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="generated parent cleanup",
        )


def test_document_storage_cleanup_occurs_only_after_successful_commit(
    ai_test_factory,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from app.services import document_storage, documents as documents_service

    settings = _settings(document_storage_path=tmp_path)
    monkeypatch.setattr(documents_service, "get_settings", lambda: settings)
    monkeypatch.setattr(document_storage, "get_settings", lambda: settings)
    user_id = None
    try:
        with ai_test_factory() as session:
            with session.begin():
                user = _create_user(session, "storage-commit")
                document, _, _ = _create_document(
                    session, user=user, suffix="storage-commit"
                )
                document_id, storage_key = document.id, document.storage_key
                user_id = user.id
        private_file = tmp_path / storage_key
        private_file.write_bytes(b"isolated test storage")

        with ai_test_factory() as session:
            def fail_before_commit(_session):
                raise OperationalError("commit", {}, RuntimeError("forced test failure"))

            event.listen(session, "before_commit", fail_before_commit)
            try:
                with pytest.raises(OperationalError):
                    delete_document(
                        session, user_id=user_id, document_id=document_id
                    )
            finally:
                event.remove(session, "before_commit", fail_before_commit)
        assert private_file.exists()
        with ai_test_factory() as session:
            assert session.get(Document, document_id) is not None

        real_remove = documents_service.remove_storage_file

        def assert_committed_then_remove(key: str) -> None:
            with ai_test_factory() as verification:
                assert verification.get(Document, document_id) is None
            real_remove(key)

        monkeypatch.setattr(
            documents_service, "remove_storage_file", assert_committed_then_remove
        )
        with ai_test_factory() as session:
            delete_document(session, user_id=user_id, document_id=document_id)
        assert not private_file.exists()
    finally:
        _cleanup_test_user(
            ai_test_factory,
            user_id,
            test_failed=sys.exc_info()[0] is not None,
            description="document storage commit boundary",
        )
