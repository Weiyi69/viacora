import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from app.api.v1.dependencies import current_user, require_csrf
from app.api.v1.routes import study_materials as study_material_routes
from app.api.v1.routes.study_materials import (
    MAX_STUDY_MATERIAL_JOB_PAGE,
    router as study_materials_router,
)
from app.schemas.study_materials import (
    CLINICAL_SAFETY_NOTICE,
    GeneratedNotePoint,
    GeneratedNoteSection,
    GeneratedStudyNotes,
    StudyMaterialResponse,
    StudyNotesGenerationRequest,
)
from app.security.request_limits import RequestBodyLimitMiddleware
from app.services.study_material_jobs import IdempotencyConflict
from app.services.ai_retrieval import RetrievalPassage
from app.services.study_material_generation import (
    _build_input,
    _content_and_citations,
)


def _passage() -> RetrievalPassage:
    return RetrievalPassage(
        document_id=uuid4(),
        generation_id=uuid4(),
        chunk_index=3,
        document_name="nursing.pdf",
        page_start=8,
        page_end=9,
        text="A nursing excerpt.",
        similarity=0.8,
    )


def test_generation_request_normalizes_default_and_rejects_duplicate_sources() -> None:
    document_id = uuid4()
    request = StudyNotesGenerationRequest(
        document_ids=[document_id],
        detail_level=" Standard ",
    )
    assert request.detail_level == "standard"

    with pytest.raises(ValidationError):
        StudyNotesGenerationRequest(document_ids=[document_id, document_id])
    with pytest.raises(ValidationError):
        StudyNotesGenerationRequest(
            document_ids=[document_id],
            detail_level="custom",
        )
    with pytest.raises(ValidationError):
        StudyNotesGenerationRequest(
            document_ids=[document_id],
            prompt="custom prompt",
        )


def test_generated_notes_validate_citations_and_map_exact_chunk_provenance() -> None:
    passage = _passage()
    notes = GeneratedStudyNotes(
        title="Medication safety",
        overview=GeneratedNotePoint(
            text="Medication checks reduce preventable errors.",
            citation_keys=["P0001"],
        ),
        sections=[
            GeneratedNoteSection(
                heading="Safety",
                points=[
                    GeneratedNotePoint(
                        text="Verify the medication against the prescribed record.",
                        citation_keys=["P0001"],
                    )
                ],
            )
        ],
    )

    content, citations = _content_and_citations(
        notes,
        {"P0001": passage},
        "standard",
    )

    assert content["overview"]["citation_keys"] == ["P0001"]
    assert citations[0].document_id == passage.document_id
    assert citations[0].generation_id == passage.generation_id
    assert citations[0].chunk_index == 3


def test_unknown_generated_citation_is_rejected() -> None:
    notes = GeneratedStudyNotes(
        title="Notes",
        overview=GeneratedNotePoint(text="Overview.", citation_keys=["P9999"]),
        sections=[
            GeneratedNoteSection(
                heading="Section",
                points=[
                    GeneratedNotePoint(text="Claim.", citation_keys=["P9999"])
                ],
            )
        ],
    )

    with pytest.raises(ValueError, match="unknown citation"):
        _content_and_citations(notes, {"P0001": _passage()}, "standard")


def test_generation_context_keeps_page_and_citation_identifiers() -> None:
    passage = _passage()

    input_text, source_passages = _build_input([passage], "detailed")

    assert "P0001" in input_text
    assert '"page_start":8' in input_text
    assert source_passages["P0001"] == passage


def test_study_material_routes_are_registered() -> None:
    paths = {route.path for route in study_materials_router.routes}
    assert "/study-kits/{study_kit_id}/study-material-jobs" in paths
    assert "/study-material-jobs/{job_id}" in paths
    assert "/study-materials/{material_id}" in paths


def test_worker_round_robins_document_index_and_material_work(monkeypatch) -> None:
    from app.services import document_worker

    calls: list[str] = []

    class StopWorker(Exception):
        pass

    def process(name: str):
        def run(_session_factory) -> bool:
            calls.append(name)
            if len(calls) == 6:
                raise StopWorker
            return True

        return run

    monkeypatch.setattr(document_worker, "reconcile_orphaned_files", lambda _factory: None)
    monkeypatch.setattr(document_worker, "process_next_document", process("document"))
    monkeypatch.setattr(document_worker, "process_next_index_job", process("index"))
    monkeypatch.setattr(
        document_worker,
        "process_next_study_material_job",
        process("material"),
    )

    with pytest.raises(StopWorker):
        document_worker.run_worker(object(), poll_interval_seconds=0)

    assert calls == ["document", "index", "material", "document", "index", "material"]


def test_study_material_enqueue_body_limit_rejects_before_route_processing() -> None:
    route_called = False
    sent_messages: list[dict[str, object]] = []

    async def app(_scope, _receive, _send) -> None:
        nonlocal route_called
        route_called = True

    async def receive() -> dict[str, object]:
        raise AssertionError("Oversized declared request must not be read")

    async def send(message: dict[str, object]) -> None:
        sent_messages.append(message)

    middleware = RequestBodyLimitMiddleware(
        app,
        max_bytes=1024,
        allowed_origins=set(),
        study_material_max_bytes=2048,
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/api/v1/study-kits/kit-id/study-material-jobs",
        "headers": [(b"content-length", b"2049")],
    }

    asyncio.run(middleware(scope, receive, send))

    assert route_called is False
    assert sent_messages[0]["status"] == 413
    assert sent_messages[1]["body"] == (
        b'{"detail":"Study-material request exceeds the maximum size"}'
    )


def test_enqueue_rate_limit_counts_new_keys_but_allows_existing_key_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    kit_id = uuid4()
    generated_job = SimpleNamespace(
        id=uuid4(),
        study_kit_id=kit_id,
        material_id=None,
        kind="notes",
        status="queued",
        request_payload={"detail_level": "standard"},
        error_code=None,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
    )
    monkeypatch.setattr(
        study_material_routes,
        "get_settings",
        lambda: SimpleNamespace(
            ai_enabled=True,
            study_material_enqueue_limit=2,
            rate_limit_window_seconds=900,
        ),
    )
    window_expiration = datetime.now(timezone.utc) + timedelta(seconds=300.5)
    enqueue_calls: list[tuple[object, ...]] = []

    def enqueue(_session, **kwargs):
        enqueue_calls.append((kwargs["user_id"], kwargs["idempotency_key"]))
        return generated_job

    monkeypatch.setattr(study_material_routes, "enqueue_study_notes", enqueue)
    counters: dict[str, int] = {}
    checked_limits: list[object] = []

    def check(limits) -> tuple[bool, datetime]:
        limit = limits[0]
        checked_limits.append(limit)
        counters[limit.identity] = counters.get(limit.identity, 0) + 1
        return counters[limit.identity] <= limit.maximum, window_expiration

    monkeypatch.setattr(
        study_material_routes, "check_rate_limits_with_expiry", check
    )
    scalar_calls = 0

    def new_request_scalar(_statement):
        nonlocal scalar_calls
        scalar_calls += 1
        return user_id if scalar_calls % 2 else None

    session = SimpleNamespace(scalar=new_request_scalar)
    user = SimpleNamespace(id=user_id)
    payload = StudyNotesGenerationRequest(document_ids=[uuid4()])

    for key in ("new-key-one", "new-key-two"):
        response = study_material_routes.post_study_notes_job(
            study_kit_id=kit_id,
            payload=payload,
            idempotency_key=key,
            user=user,
            session=session,
        )
        assert response.status == "queued"

    with pytest.raises(HTTPException) as limited:
        study_material_routes.post_study_notes_job(
            study_kit_id=kit_id,
            payload=payload,
            idempotency_key="new-key-three",
            user=user,
            session=session,
        )
    assert limited.value.status_code == 429
    assert limited.value.headers["Retry-After"] == "301"
    assert len(checked_limits) == 3
    assert {limit.identity for limit in checked_limits} == {str(user_id)}
    assert len(enqueue_calls) == 2
    assert all(call[0] == user_id for call in enqueue_calls)

    existing_session = SimpleNamespace(scalar=lambda _statement: uuid4())

    def reject_if_rate_checked(_limits) -> tuple[bool, datetime]:
        raise AssertionError("Existing idempotency keys must not consume quota")

    monkeypatch.setattr(
        study_material_routes,
        "check_rate_limits_with_expiry",
        reject_if_rate_checked,
    )
    replay = study_material_routes.post_study_notes_job(
        study_kit_id=kit_id,
        payload=payload,
        idempotency_key="new-key-one",
        user=user,
        session=existing_session,
    )
    assert replay.status == "queued"

    def conflicting_replay(_session, **_kwargs):
        raise IdempotencyConflict

    monkeypatch.setattr(
        study_material_routes, "enqueue_study_notes", conflicting_replay
    )
    with pytest.raises(HTTPException) as conflict:
        study_material_routes.post_study_notes_job(
            study_kit_id=kit_id,
            payload=payload,
            idempotency_key="new-key-one",
            user=user,
            session=existing_session,
        )
    assert conflict.value.status_code == 409


@pytest.mark.parametrize(
    ("seconds_remaining", "expected_retry_after"),
    [(899.01, 900), (0.01, 1), (0, 1), (-5, 1)],
)
def test_retry_after_uses_positive_rounded_up_window_time(
    seconds_remaining: float,
    expected_retry_after: int,
) -> None:
    now = datetime(2026, 11, 10, 0, 0, tzinfo=timezone.utc)
    expires_at = now + timedelta(seconds=seconds_remaining)

    assert (
        study_material_routes._retry_after_seconds(expires_at, now=now)
        == expected_retry_after
    )


def test_study_material_page_number_has_a_bounded_openapi_contract() -> None:
    app = FastAPI()
    app.include_router(study_materials_router, prefix="/api/v1")

    parameters = app.openapi()["paths"][
        "/api/v1/study-kits/{study_kit_id}/study-material-jobs"
    ]["get"]["parameters"]
    page_schema = next(
        parameter["schema"] for parameter in parameters if parameter["name"] == "page"
    )

    assert page_schema["minimum"] == 1
    assert page_schema["maximum"] == MAX_STUDY_MATERIAL_JOB_PAGE == 1000


def test_material_response_discloses_exact_unreviewed_clinical_safety_notice() -> None:
    response = StudyMaterialResponse(
        id=uuid4(),
        study_kit_id=uuid4(),
        job_id=uuid4(),
        kind="notes",
        schema_version=1,
        version_number=1,
        title="Study notes",
        content={"sections": []},
        citations=[],
        created_at=datetime.now(timezone.utc),
    )

    assert response.ai_generated is True
    assert response.review_status == "unreviewed"
    assert response.safety_notice == (
        "AI-generated, unreviewed educational draft. Citations identify retrieved source "
        "passages only; they do not establish that a clinical statement is supported, "
        "accurate, current, or safe. Qualified nursing/clinical review is required before "
        "educational or clinical reliance."
    )
    assert response.safety_notice == CLINICAL_SAFETY_NOTICE


def test_study_material_routes_keep_authentication_and_csrf_dependencies() -> None:
    for route in study_materials_router.routes:
        dependency_calls = {dependency.call for dependency in route.dependant.dependencies}
        assert current_user in dependency_calls
        if "POST" in route.methods:
            assert require_csrf in dependency_calls


def test_study_material_list_uses_authenticated_user_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    kit_id = uuid4()
    observed: dict[str, object] = {}

    def list_jobs(_session, **kwargs):
        observed.update(kwargs)
        return [], 0

    monkeypatch.setattr(
        study_material_routes, "list_study_material_jobs", list_jobs
    )
    response = study_material_routes.get_study_kit_material_jobs(
        study_kit_id=kit_id,
        page=1,
        page_size=20,
        user=SimpleNamespace(id=user_id),
        session=object(),
    )

    assert response.items == []
    assert observed["user_id"] == user_id
    assert observed["study_kit_id"] == kit_id
