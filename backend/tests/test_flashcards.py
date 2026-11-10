import asyncio
from contextlib import nullcontext
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import FastAPI, HTTPException
from pydantic import ValidationError

from app.api.v1.dependencies import current_user, require_csrf
from app.api.v1.routes import study_materials as routes
from app.api.v1.routes.study_materials import router
from app.models import (
    FlashcardReviewEvent,
    StudyMaterialCard,
    StudyMaterialCardCitation,
)
from app.schemas.study_materials import (
    FlashcardGroundingAssessment,
    FlashcardClaimSupportCheck,
    FlashcardGenerationRequest,
    FlashcardReviewRequest,
    GeneratedFlashcard,
    GeneratedFlashcards,
    StudyMaterialCardResponse,
)
from app.security.request_limits import RequestBodyLimitMiddleware
from app.services import flashcard_learning
from app.services.ai_retrieval import RetrievalPassage
from app.ai.provider import AIProviderError, StructuredOutputResult
from app.core.config import Settings
from app.services import study_material_generation
from app.services.study_material_generation import (
    _process_lease,
    _flashcards_and_citations,
    _verify_flashcard_support,
)
from app.services.study_material_jobs import StudyMaterialLease


def _passage(
    text: str = "The passage explains the learning concept.",
) -> RetrievalPassage:
    return RetrievalPassage(
        document_id=uuid4(),
        generation_id=uuid4(),
        chunk_index=4,
        document_name="course.pdf",
        page_start=12,
        page_end=13,
        text=text,
        similarity=0.9,
    )


def test_flashcard_schemas_bound_requests_and_require_citations() -> None:
    request = FlashcardGenerationRequest(document_ids=[uuid4()], card_count=30)
    assert request.card_count == 30
    with pytest.raises(ValidationError):
        FlashcardGenerationRequest(document_ids=[uuid4()], card_count=31)
    with pytest.raises(ValidationError):
        FlashcardGenerationRequest(document_ids=[uuid4()], card_count=True)
    with pytest.raises(ValidationError):
        GeneratedFlashcard(front="Question", back="Answer", citation_keys=[])
    with pytest.raises(ValidationError):
        FlashcardReviewRequest(rating="clinical_verified")
    card_response = StudyMaterialCardResponse(
        id=uuid4(),
        position=0,
        front="Question",
        back="Answer",
        explanation=None,
        difficulty="easy",
        tags=[],
        citation_keys=["P0001"],
    )
    assert card_response.ai_generated is True
    assert card_response.review_status == "unreviewed"


def test_generated_flashcards_require_exact_count_and_known_passages() -> None:
    passage = _passage()
    generated = GeneratedFlashcards(
        title="Review deck",
        cards=[
            GeneratedFlashcard(
                front="What does the passage explain?",
                back="The learning concept.",
                citation_keys=["P0001"],
            )
        ],
    )

    content, citations, cards = _flashcards_and_citations(
        generated,
        {"P0001": passage},
        1,
    )

    assert content == {"card_count": 1}
    assert cards[0].position == 0
    assert cards[0].citation_keys == ["P0001"]
    assert citations[0].document_id == passage.document_id
    assert citations[0].generation_id == passage.generation_id
    assert citations[0].chunk_index == passage.chunk_index
    with pytest.raises(ValueError, match="unknown citation"):
        _flashcards_and_citations(
            generated,
            {},
            1,
        )
    fewer_than_requested = _flashcards_and_citations(
        generated,
        {"P0001": passage},
        2,
    )
    assert len(fewer_than_requested[2]) == 1
    duplicate = GeneratedFlashcards(
        title="Duplicate deck",
        cards=[
            GeneratedFlashcard(
                front="What does the source explain?",
                back="The learning concept.",
                citation_keys=["P0001"],
            ),
            GeneratedFlashcard(
                front="What does the source explain",
                back="A different answer.",
                citation_keys=["P0001"],
            ),
        ],
    )
    with pytest.raises(ValueError, match="exceeds"):
        _flashcards_and_citations(duplicate, {"P0001": passage}, 1)
    with pytest.raises(ValueError, match="near-duplicate"):
        _flashcards_and_citations(duplicate, {"P0001": passage}, 2)


def _grounding_case(
    back: str = "Cell division produces two daughter cells.",
) -> tuple[GeneratedFlashcards, dict[str, RetrievalPassage]]:
    return (
        GeneratedFlashcards(
            title="Grounded deck",
            cards=[
                GeneratedFlashcard(
                    front="What does the passage explain?",
                    back=back,
                    citation_keys=["P0001"],
                )
            ],
        ),
        {
            "P0001": _passage(
                "Cell division produces two daughter cells. The process produces "
                "genetically identical cells."
            )
        },
    )


def _grounding_settings() -> Settings:
    return Settings(
        _env_file=None,
        ai_enabled=True,
        openai_api_key="test-key",
        ai_pricing_version="test-v1",
        ai_embedding_input_usd_per_million="0.02",
        ai_structured_input_usd_per_million="0.15",
        ai_structured_output_usd_per_million="0.60",
    )


def _install_fake_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[object], list[object]]:
    reservations: list[object] = []
    settlements: list[object] = []

    class SessionContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    def reserve(_session, **kwargs):
        reservation = SimpleNamespace(
            id=uuid4(), operation=kwargs["operation"], key=kwargs["idempotency_key"]
        )
        reservations.append(reservation)
        return reservation

    monkeypatch.setattr(study_material_generation, "reserve_usage", reserve)
    monkeypatch.setattr(
        study_material_generation, "mark_dispatching", lambda *_args: None
    )
    monkeypatch.setattr(
        study_material_generation,
        "settle_usage",
        lambda _session, reservation_id, **kwargs: settlements.append(
            (reservation_id, kwargs)
        ),
    )
    return reservations, settlements


class _AssessmentProvider:
    def __init__(self, assessments: list[object]) -> None:
        self.assessments = assessments
        self.calls = 0

    def structured_output(self, *_args, **_kwargs):
        assessment = self.assessments[self.calls]
        self.calls += 1
        if isinstance(assessment, Exception):
            raise assessment
        if isinstance(assessment, StructuredOutputResult):
            return assessment
        return StructuredOutputResult(
            value=assessment,
            input_tokens=20,
            output_tokens=8,
            provider_request_id=f"request-{self.calls}",
        )


def _assessment(*supported: bool) -> FlashcardGroundingAssessment:
    return FlashcardGroundingAssessment(
        checks=[
            FlashcardClaimSupportCheck(
                card_position=0,
                field="front",
                claim_index=0,
                supported=True,
            ),
            *[
                FlashcardClaimSupportCheck(
                    card_position=0,
                    field="back",
                    claim_index=index,
                    supported=value,
                )
                for index, value in enumerate(supported)
            ],
        ]
    )


def _verify(
    monkeypatch: pytest.MonkeyPatch,
    flashcards: GeneratedFlashcards,
    source_passages: dict[str, RetrievalPassage],
    provider: _AssessmentProvider,
) -> tuple[list[object], list[object]]:
    reservations, settlements = _install_fake_usage(monkeypatch)

    class SessionContext:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    _verify_flashcard_support(
        lambda: SessionContext(),
        lease=SimpleNamespace(user_id=uuid4(), job_id=uuid4(), attempts_used=1),
        flashcards=flashcards,
        source_passages=source_passages,
        provider=provider,
        settings=_grounding_settings(),
    )
    return reservations, settlements


def test_flashcard_grounding_accepts_supported_claims_and_accounts_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flashcards, passages = _grounding_case(
        "Cell division produces two daughter cells. It produces genetically "
        "identical cells."
    )
    provider = _AssessmentProvider([_assessment(True, True)])

    reservations, settlements = _verify(monkeypatch, flashcards, passages, provider)

    assert provider.calls == 1
    assert reservations[0].operation == "study_flashcard_verification"
    assert len(settlements) == 1
    assert settlements[0][1]["input_tokens"] == 20
    assert settlements[0][1]["output_tokens"] == 8


@pytest.mark.parametrize(
    ("back", "supported"),
    [
        ("The passage explains an unrelated fact.", (False,)),
        (
            "Cell division produces two daughter cells. A second unsupported claim.",
            (True, False),
        ),
        (
            "Cell division produces two daughter cells. It occurs in two phases.",
            (True, False),
        ),
    ],
)
def test_flashcard_grounding_rejects_unsupported_or_partially_supported_claims(
    monkeypatch: pytest.MonkeyPatch,
    back: str,
    supported: tuple[bool, ...],
) -> None:
    flashcards, passages = _grounding_case(back)
    provider = _AssessmentProvider([_assessment(*supported)])

    with pytest.raises(ValueError, match="unsupported claim"):
        _verify(monkeypatch, flashcards, passages, provider)


def test_flashcard_grounding_retries_malformed_assessment_with_bounded_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    flashcards, passages = _grounding_case()
    provider = _AssessmentProvider([_assessment(), _assessment()])
    reservations, settlements = _install_fake_usage(monkeypatch)

    with pytest.raises(ValueError, match="assessment is invalid"):
        _verify_flashcard_support(
            lambda: nullcontext(SimpleNamespace()),
            lease=SimpleNamespace(user_id=uuid4(), job_id=uuid4(), attempts_used=1),
            flashcards=flashcards,
            source_passages=passages,
            provider=provider,
            settings=_grounding_settings(),
        )

    assert provider.calls == 2
    assert len(reservations) == 2
    assert len(settlements) == 2
    assert reservations[0].key != reservations[1].key


@pytest.mark.parametrize(
    "error",
    [
        AIProviderError("provider_temporarily_unavailable", retryable=True),
        TimeoutError(),
    ],
)
def test_flashcard_grounding_provider_failures_preserve_uncertain_usage(
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
) -> None:
    flashcards, passages = _grounding_case()
    provider = _AssessmentProvider([error])
    reservations, _ = _install_fake_usage(monkeypatch)
    uncertain: list[object] = []
    monkeypatch.setattr(
        study_material_generation,
        "mark_uncertain",
        lambda _session, reservation_id: uncertain.append(reservation_id),
    )

    with pytest.raises(AIProviderError):
        _verify_flashcard_support(
            lambda: nullcontext(SimpleNamespace()),
            lease=SimpleNamespace(user_id=uuid4(), job_id=uuid4(), attempts_used=1),
            flashcards=flashcards,
            source_passages=passages,
            provider=provider,
            settings=_grounding_settings(),
        )

    assert len(reservations) == 1
    assert uncertain == [reservations[0].id]


def test_unsupported_flashcard_claim_never_reaches_finalization(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    passage = _passage()
    generated = GeneratedFlashcards(
        title="Rejected deck",
        cards=[
            GeneratedFlashcard(
                front="What does the passage explain?",
                back="An unsupported claim.",
                citation_keys=["P0001"],
            )
        ],
    )
    provider = _AssessmentProvider(
        [
            StructuredOutputResult(
                value=generated,
                input_tokens=25,
                output_tokens=20,
                provider_request_id="generation-request",
            ),
            _assessment(False),
        ]
    )
    _install_fake_usage(monkeypatch)
    monkeypatch.setattr(
        study_material_generation,
        "_generation_context",
        lambda *_args: ({passage.document_id: passage.generation_id}, [], "flashcards", "standard", 1),
    )
    monkeypatch.setattr(
        study_material_generation,
        "retrieve_passages",
        lambda *_args, **_kwargs: [passage],
    )

    class Heartbeat:
        lost = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    monkeypatch.setattr(
        study_material_generation, "_LeaseHeartbeat", lambda *_args: Heartbeat()
    )
    failed: list[dict[str, object]] = []
    finalized: list[object] = []
    monkeypatch.setattr(
        study_material_generation,
        "fail_study_material_job",
        lambda *_args, **kwargs: failed.append(kwargs),
    )
    monkeypatch.setattr(
        study_material_generation,
        "finalize_study_flashcard_job",
        lambda *_args, **kwargs: finalized.append(kwargs),
    )

    lease = StudyMaterialLease(
        job_id=uuid4(),
        user_id=uuid4(),
        study_space_id=uuid4(),
        study_kit_id=uuid4(),
        token=uuid4(),
        attempts_used=1,
    )
    _process_lease(
        lambda: nullcontext(SimpleNamespace()),
        lease,
        settings=_grounding_settings(),
        provider_factory=lambda _settings: provider,
    )

    assert failed == [{"error_code": "invalid_generation_result", "retryable": False}]
    assert finalized == []


def test_flashcard_database_constraints_cover_order_and_same_material_citations() -> None:
    card_constraints = list(StudyMaterialCard.__table__.constraints)
    assert any(
        constraint.name == "uq_study_material_cards_material_position"
        for constraint in card_constraints
    )
    assert any(
        constraint.name == "ck_study_material_cards_material_kind"
        for constraint in card_constraints
    )
    assert any(
        constraint.name == "uq_study_materials_id_kind"
        for constraint in StudyMaterialCard.__table__.metadata.tables[
            "study_materials"
        ].constraints
    )
    card_material_kind_fks = [
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in StudyMaterialCard.__table__.foreign_key_constraints
    ]
    assert ("study_materials.id", "study_materials.kind") in card_material_kind_fks
    citation_fks = [
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in StudyMaterialCardCitation.__table__.foreign_key_constraints
    ]
    assert (
        "study_material_cards.material_id",
        "study_material_cards.id",
    ) in citation_fks
    assert (
        "study_material_citations.study_material_id",
        "study_material_citations.id",
    ) in citation_fks
    review_fks = [
        tuple(element.target_fullname for element in constraint.elements)
        for constraint in FlashcardReviewEvent.__table__.foreign_key_constraints
    ]
    assert ("study_material_cards.user_id", "study_material_cards.id") in review_fks


def test_flashcard_enqueue_body_limit_applies_before_route_processing() -> None:
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
        "path": "/api/v1/study-kits/kit-id/study-material-jobs/flashcards",
        "headers": [(b"content-length", b"2049")],
    }

    asyncio.run(middleware(scope, receive, send))

    assert not route_called
    assert sent_messages[0]["status"] == 413


def test_flashcard_enqueue_throttles_different_idempotency_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    kit_id = uuid4()
    job = SimpleNamespace(
        id=uuid4(),
        study_kit_id=kit_id,
        material_id=None,
        kind="flashcards",
        status="queued",
        request_payload={"card_count": 20},
        error_code=None,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
    )
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(ai_enabled=True, study_material_enqueue_limit=1),
    )
    enqueued: list[str] = []
    monkeypatch.setattr(
        routes,
        "enqueue_flashcards",
        lambda _session, **kwargs: enqueued.append(kwargs["idempotency_key"]) or job,
    )
    checks: list[str] = []
    monkeypatch.setattr(
        routes,
        "check_rate_limits_with_expiry",
        lambda limits: (
            checks.append(limits[0].identity) is None and len(checks) <= 1,
            datetime.now(timezone.utc),
        ),
    )
    scalar_calls = 0

    def scalar(_statement):
        nonlocal scalar_calls
        scalar_calls += 1
        return user_id if scalar_calls % 2 else None

    session = SimpleNamespace(scalar=scalar)
    user = SimpleNamespace(id=user_id)
    payload = FlashcardGenerationRequest(document_ids=[uuid4()])

    response = routes.post_flashcards_job(
        study_kit_id=kit_id,
        payload=payload,
        idempotency_key="first",
        user=user,
        session=session,
    )
    assert response.kind == "flashcards"
    with pytest.raises(HTTPException) as limited:
        routes.post_flashcards_job(
            study_kit_id=kit_id,
            payload=payload,
            idempotency_key="different-key",
            user=user,
            session=session,
        )
    assert limited.value.status_code == 429
    assert enqueued == ["first"]
    assert checks == [str(user_id), str(user_id)]


def test_flashcard_enqueue_retry_keeps_existing_idempotency_behavior(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    existing_job_id = uuid4()
    job = SimpleNamespace(
        id=existing_job_id,
        study_kit_id=uuid4(),
        material_id=None,
        kind="flashcards",
        status="queued",
        request_payload={"card_count": 20},
        error_code=None,
        created_at=datetime.now(timezone.utc),
        started_at=None,
        completed_at=None,
    )
    monkeypatch.setattr(
        routes,
        "get_settings",
        lambda: SimpleNamespace(ai_enabled=True, study_material_enqueue_limit=1),
    )
    monkeypatch.setattr(
        routes,
        "check_rate_limits_with_expiry",
        lambda _limits: (_ for _ in ()).throw(
            AssertionError("An existing key must not consume enqueue quota")
        ),
    )
    monkeypatch.setattr(routes, "enqueue_flashcards", lambda *_args, **_kwargs: job)
    scalar_calls = 0

    def scalar(_statement):
        nonlocal scalar_calls
        scalar_calls += 1
        return user_id if scalar_calls == 1 else existing_job_id

    response = routes.post_flashcards_job(
        study_kit_id=job.study_kit_id,
        payload=FlashcardGenerationRequest(document_ids=[uuid4()]),
        idempotency_key="existing-key",
        user=SimpleNamespace(id=user_id),
        session=SimpleNamespace(scalar=scalar),
    )
    assert response.id == existing_job_id


class _ReviewSession:
    def __init__(self, existing=None) -> None:
        self.existing = existing
        self.added = None
        self.committed = False

    def scalar(self, _statement):
        return self.existing

    def begin_nested(self):
        return nullcontext()

    def add(self, event) -> None:
        self.added = event

    def flush(self) -> None:
        return None

    def commit(self) -> None:
        self.committed = True


def test_review_idempotency_returns_existing_or_conflicts_without_overwrite(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id, material_id, card_id = uuid4(), uuid4(), uuid4()
    monkeypatch.setattr(
        flashcard_learning,
        "_owned_flashcard",
        lambda *_args, **_kwargs: SimpleNamespace(id=card_id),
    )
    session = _ReviewSession()
    created = flashcard_learning.create_review_event(
        session,
        user_id=user_id,
        material_id=material_id,
        card_id=card_id,
        rating="remembered",
        idempotency_key="review-key",
    )
    assert created is session.added
    assert session.committed
    assert created.rating == "remembered"

    replay_session = _ReviewSession(existing=created)
    replay = flashcard_learning.create_review_event(
        replay_session,
        user_id=user_id,
        material_id=material_id,
        card_id=card_id,
        rating="remembered",
        idempotency_key="review-key",
    )
    assert replay is created
    assert replay_session.added is None

    with pytest.raises(flashcard_learning.ReviewIdempotencyConflict):
        flashcard_learning.create_review_event(
            _ReviewSession(existing=created),
            user_id=user_id,
            material_id=material_id,
            card_id=uuid4(),
            rating="needs_review",
            idempotency_key="review-key",
        )


def test_flashcard_routes_require_authentication_and_csrf_for_writes() -> None:
    app = FastAPI()
    app.include_router(router, prefix="/api/v1")
    paths = app.openapi()["paths"]
    assert "/api/v1/study-kits/{study_kit_id}/study-material-jobs/flashcards" in paths
    assert "/api/v1/study-materials/{material_id}/progress" in paths
    assert (
        "/api/v1/study-materials/{material_id}/cards/{card_id}/reviews" in paths
    )
    assert "/api/v1/study-materials/flashcards" in paths
    for route in router.routes:
        dependencies = {item.call for item in route.dependant.dependencies}
        assert current_user in dependencies
        if "POST" in route.methods:
            assert require_csrf in dependencies


def test_flashcard_deck_listing_is_authenticated_owner_scoped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    user_id = uuid4()
    calls: list[dict[str, object]] = []

    def list_decks(_session, **kwargs):
        calls.append(kwargs)
        return [], 0

    monkeypatch.setattr(routes, "list_study_flashcard_materials", list_decks)
    response = routes.get_flashcard_decks(
        page=2,
        page_size=10,
        user=SimpleNamespace(id=user_id),
        session=SimpleNamespace(),
    )

    assert response.items == []
    assert response.page == 2
    assert calls == [
        {"user_id": user_id, "page": 2, "page_size": 10}
    ]
