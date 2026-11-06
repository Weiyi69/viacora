import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.ai.openai_provider import OpenAIProvider
from app.ai.provider import AIProviderError
from app.core.config import Settings, get_settings
from app.models import (
    AIEmbeddingProfile,
    AIUsageEvent,
    Document,
    DocumentChunk,
    DocumentChunkEmbedding,
    DocumentIndexGeneration,
    DocumentIndexHead,
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


@dataclass(frozen=True)
class RetrievalPassage:
    document_id: UUID
    document_name: str
    page_start: int
    page_end: int
    text: str
    similarity: float


@dataclass(frozen=True)
class UsageSummary:
    period_start: datetime
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    event_count: int


def _configured_profile(session: Session, settings: Settings) -> AIEmbeddingProfile:
    profile = session.scalar(
        select(AIEmbeddingProfile).where(AIEmbeddingProfile.singleton_key.is_(True))
    )
    if profile is None or (
        profile.profile_id != settings.ai_embedding_profile_id
        or profile.provider != "openai"
        or profile.model_name != settings.ai_embedding_model
        or profile.dimensions != settings.ai_embedding_dimensions
        or profile.distance_metric != "cosine"
    ):
        raise RuntimeError("AI embedding profile configuration does not match the database")
    return profile


def embed_query(
    session: Session,
    *,
    user_id: UUID,
    query: str,
    settings: Settings | None = None,
    provider_factory=OpenAIProvider,
) -> tuple[float, ...]:
    settings = settings or get_settings()
    if not settings.ai_enabled:
        raise RuntimeError("AI provider is disabled")
    _configured_profile(session, settings)
    query_bytes = len(query.encode("utf-8"))
    if query_bytes > settings.ai_max_request_bytes:
        raise ValueError("Query exceeds the configured request byte limit")

    last_error: AIProviderError | None = None
    for attempt in range(settings.ai_provider_max_retries + 1):
        reservation = reserve_usage(
            session,
            user_id=user_id,
            operation="query_embedding",
            idempotency_key=f"query:{uuid4()}",
            input_token_upper_bound=query_bytes,
            settings=settings,
        )
        mark_dispatching(session, reservation.id)
        try:
            response = provider_factory(settings).embed([query])
        except AIProviderError as error:
            if error.error_code == "provider_request_rejected":
                release_known_nonbillable(session, reservation.id)
            else:
                mark_uncertain(session, reservation.id)
            last_error = error
            if not error.retryable or attempt >= settings.ai_provider_max_retries:
                raise
            time.sleep(
                min(
                    settings.ai_provider_retry_base_seconds * (2**attempt),
                    settings.ai_provider_retry_max_seconds,
                )
            )
            continue
        except Exception:
            mark_uncertain(session, reservation.id)
            raise

        settle_usage(
            session,
            reservation.id,
            input_tokens=response.input_tokens,
            provider_request_id=response.provider_request_id,
            conservative_estimate=response.input_tokens is None,
            settings=settings,
        )
        return response.vectors[0]
    if last_error is None:
        raise RuntimeError("Query embedding failed without a provider response")
    raise last_error


def retrieve_passages(
    session: Session,
    *,
    user_id: UUID,
    query: str,
    document_ids: list[UUID] | None = None,
    top_k: int = 5,
    settings: Settings | None = None,
    provider_factory=OpenAIProvider,
) -> list[RetrievalPassage]:
    settings = settings or get_settings()
    if not 1 <= top_k <= settings.ai_retrieval_max_top_k:
        raise ValueError("Retrieval result limit is invalid")
    normalized_query = query.strip()
    if not normalized_query or len(normalized_query) > settings.ai_query_max_chars:
        raise ValueError("Retrieval query is empty or exceeds its character limit")
    vector = embed_query(
        session,
        user_id=user_id,
        query=normalized_query,
        settings=settings,
        provider_factory=provider_factory,
    )
    profile = _configured_profile(session, settings)
    similarity = 1 - DocumentChunkEmbedding.embedding.cosine_distance(list(vector))
    statement = (
        select(
            DocumentChunk.text,
            Document.id,
            Document.original_filename,
            DocumentChunk.page_start,
            DocumentChunk.page_end,
            similarity.label("similarity"),
        )
        .join(
            DocumentIndexHead,
            DocumentIndexHead.document_id == Document.id,
        )
        .join(
            DocumentIndexGeneration,
            and_(
                DocumentIndexGeneration.document_id == DocumentIndexHead.document_id,
                DocumentIndexGeneration.embedding_profile_id == DocumentIndexHead.profile_id,
                DocumentIndexGeneration.id == DocumentIndexHead.generation_id,
            ),
        )
        .join(
            Subject,
            and_(
                Subject.id == Document.subject_id,
                Subject.user_id == user_id,
            ),
        )
        .join(
            StudySpace,
            and_(
                StudySpace.id == Document.study_space_id,
                StudySpace.subject_id == Subject.id,
            ),
        )
        .join(
            DocumentChunk,
            and_(
                DocumentChunk.document_id == Document.id,
                DocumentChunk.generation_id == DocumentIndexGeneration.id,
            ),
        )
        .join(
            DocumentChunkEmbedding,
            and_(
                DocumentChunkEmbedding.document_id == DocumentChunk.document_id,
                DocumentChunkEmbedding.generation_id == DocumentChunk.generation_id,
                DocumentChunkEmbedding.chunk_index == DocumentChunk.chunk_index,
                DocumentChunkEmbedding.profile_id == DocumentIndexGeneration.embedding_profile_id,
            ),
        )
        .join(
            AIEmbeddingProfile,
            and_(
                AIEmbeddingProfile.singleton_key.is_(True),
                AIEmbeddingProfile.profile_id == DocumentIndexHead.profile_id,
            ),
        )
        .where(
            Document.user_id == user_id,
            Document.status == "completed",
            Document.has_extractable_text.is_(True),
            Subject.archived_at.is_(None),
            StudySpace.archived_at.is_(None),
            DocumentIndexGeneration.status == "ready",
            DocumentIndexGeneration.dimensions == settings.ai_embedding_dimensions,
            AIEmbeddingProfile.profile_id == profile.profile_id,
            similarity >= settings.ai_retrieval_min_similarity,
        )
        .order_by(DocumentChunkEmbedding.embedding.cosine_distance(list(vector)))
        .limit(top_k)
    )
    if document_ids is not None:
        statement = statement.where(Document.id.in_(document_ids))
    rows = session.execute(statement).all()
    return [
        RetrievalPassage(
            document_id=document_id,
            document_name=filename,
            page_start=page_start,
            page_end=page_end,
            text=text_value,
            similarity=float(score),
        )
        for text_value, document_id, filename, page_start, page_end, score in rows
    ]


def usage_summary(
    session: Session,
    *,
    user_id: UUID,
    days: int = 1,
) -> UsageSummary:
    if not 1 <= days <= 30:
        raise ValueError("Usage period must be between 1 and 30 days")
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    totals = session.execute(
        select(
            func.coalesce(func.sum(AIUsageEvent.input_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.output_tokens), 0),
            func.coalesce(func.sum(AIUsageEvent.estimated_cost_usd), 0),
            func.count(AIUsageEvent.id),
        ).where(
            AIUsageEvent.user_id == user_id,
            AIUsageEvent.occurred_at >= start,
        )
    ).one()
    return UsageSummary(
        period_start=start,
        input_tokens=int(totals[0]),
        output_tokens=int(totals[1]),
        estimated_cost_usd=float(totals[2]),
        event_count=int(totals[3]),
    )
