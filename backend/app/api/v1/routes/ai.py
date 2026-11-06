import logging

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.ai.provider import AIProviderError
from app.api.v1.dependencies import current_user, database_session, require_csrf
from app.core.config import get_settings
from app.models import User
from app.schemas.ai import (
    AIUsageResponse,
    AIRetrievalPassage,
    AIRetrievalRequest,
    AIRetrievalResponse,
    AISourceReference,
)
from app.services.ai_retrieval import retrieve_passages, usage_summary
from app.services.ai_usage_reservations import UsageLimitExceeded

router = APIRouter(prefix="/ai", tags=["ai"])
logger = logging.getLogger(__name__)


@router.post(
    "/retrieval",
    response_model=AIRetrievalResponse,
    dependencies=[Depends(require_csrf)],
)
def post_ai_retrieval(
    payload: AIRetrievalRequest,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> AIRetrievalResponse:
    settings = get_settings()
    if not settings.ai_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI retrieval is unavailable",
        )
    if len(payload.query) > settings.ai_query_max_chars:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Query exceeds the maximum length",
        )
    if len(payload.query.encode("utf-8")) > settings.ai_max_request_bytes:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Query exceeds the maximum request size",
        )
    try:
        passages = retrieve_passages(
            session,
            user_id=user.id,
            query=payload.query,
            document_ids=payload.document_ids,
            top_k=payload.top_k,
            settings=settings,
        )
    except UsageLimitExceeded:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="AI usage limit reached",
            headers={"Retry-After": "60"},
        ) from None
    except RuntimeError as error:
        if str(error) == "AI provider is disabled":
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="AI retrieval is unavailable",
            ) from None
        logger.exception("AI retrieval failed due to a configuration or processing error")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI retrieval is unavailable",
        ) from None
    except AIProviderError as error:
        logger.warning("AI retrieval provider failed: %s", error.error_code)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AI retrieval is temporarily unavailable",
        ) from None
    return AIRetrievalResponse(
        passages=[
            AIRetrievalPassage(
                text=item.text,
                similarity=item.similarity,
                source=AISourceReference(
                    document_id=item.document_id,
                    document_name=item.document_name,
                    page_start=item.page_start,
                    page_end=item.page_end,
                ),
            )
            for item in passages
        ]
    )


@router.get("/usage", response_model=AIUsageResponse)
def get_ai_usage(
    days: int = Query(default=1, ge=1, le=30),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> AIUsageResponse:
    result = usage_summary(session, user_id=user.id, days=days)
    return AIUsageResponse(
        period_start=result.period_start,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        estimated_cost_usd=result.estimated_cost_usd,
        event_count=result.event_count,
    )
