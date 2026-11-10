from datetime import datetime, timezone
from math import ceil
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.v1.dependencies import current_user, database_session, require_csrf
from app.core.config import get_settings
from app.models import StudyMaterialJob, User
from app.schemas.learning import PageResponse
from app.schemas.study_materials import (
    StudyMaterialJobResponse,
    StudyMaterialResponse,
    StudyNotesGenerationRequest,
)
from app.services.database import get_session_factory
from app.services.rate_limit import (
    RateLimit,
    RateLimitUnavailable,
    check_rate_limits_with_expiry,
)
from app.services.study_material_generation import (
    enqueue_study_notes,
    get_study_material,
    get_study_material_job,
    list_study_material_jobs,
)
from app.services.study_material_jobs import (
    IdempotencyConflict,
    cancel_study_material_job,
)

router = APIRouter(tags=["study materials"])
MAX_STUDY_MATERIAL_JOB_PAGE = 1000


def _retry_after_seconds(expires_at: datetime, *, now: datetime | None = None) -> int:
    current_time = now or datetime.now(timezone.utc)
    return max(1, ceil((expires_at - current_time).total_seconds()))


def _enforce_new_enqueue_rate_limit(
    session: Session,
    *,
    user_id: UUID,
    idempotency_key: str,
) -> None:
    locked_user_id = session.scalar(
        select(User.id).where(User.id == user_id).with_for_update()
    )
    if locked_user_id is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
        )

    existing_job_id = session.scalar(
        select(StudyMaterialJob.id).where(
            StudyMaterialJob.user_id == user_id,
            StudyMaterialJob.idempotency_key == idempotency_key,
        )
    )
    if existing_job_id is not None:
        return

    settings = get_settings()
    try:
        allowed, expires_at = check_rate_limits_with_expiry(
            [
                RateLimit(
                    "study-material-enqueue-user",
                    str(user_id),
                    settings.study_material_enqueue_limit,
                )
            ]
        )
    except RateLimitUnavailable as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Study-material enqueue protection is temporarily unavailable",
        ) from error
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many study-material jobs. Try again later.",
            headers={"Retry-After": str(_retry_after_seconds(expires_at))},
        )


@router.post(
    "/study-kits/{study_kit_id}/study-material-jobs",
    response_model=StudyMaterialJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
def post_study_notes_job(
    study_kit_id: UUID,
    payload: StudyNotesGenerationRequest,
    idempotency_key: str = Header(
        min_length=1, max_length=160, alias="Idempotency-Key"
    ),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudyMaterialJobResponse:
    if not get_settings().ai_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Study material generation is unavailable",
        )
    _enforce_new_enqueue_rate_limit(
        session,
        user_id=user.id,
        idempotency_key=idempotency_key,
    )
    try:
        job = enqueue_study_notes(
            session,
            user_id=user.id,
            study_kit_id=study_kit_id,
            document_ids=payload.document_ids,
            detail_level=payload.detail_level,
            idempotency_key=idempotency_key,
        )
    except IdempotencyConflict:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency key was already used for another request",
        ) from None
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(error),
        ) from None
    return StudyMaterialJobResponse.model_validate(job)


@router.get(
    "/study-kits/{study_kit_id}/study-material-jobs",
    response_model=PageResponse[StudyMaterialJobResponse],
)
def get_study_kit_material_jobs(
    study_kit_id: UUID,
    page: int = Query(default=1, ge=1, le=MAX_STUDY_MATERIAL_JOB_PAGE),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> PageResponse[StudyMaterialJobResponse]:
    jobs, total = list_study_material_jobs(
        session,
        user_id=user.id,
        study_kit_id=study_kit_id,
        page=page,
        page_size=page_size,
    )
    return PageResponse[StudyMaterialJobResponse](
        items=[StudyMaterialJobResponse.model_validate(job) for job in jobs],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get(
    "/study-material-jobs/{job_id}",
    response_model=StudyMaterialJobResponse,
)
def get_study_material_job_status(
    job_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudyMaterialJobResponse:
    job = get_study_material_job(session, user_id=user.id, job_id=job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study material job not found",
        )
    return StudyMaterialJobResponse.model_validate(job)


@router.post(
    "/study-material-jobs/{job_id}/cancel",
    response_model=StudyMaterialJobResponse,
    dependencies=[Depends(require_csrf)],
)
def post_cancel_study_material_job(
    job_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudyMaterialJobResponse:
    cancel_study_material_job(
        get_session_factory(),
        user_id=user.id,
        job_id=job_id,
    )
    job = get_study_material_job(session, user_id=user.id, job_id=job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study material job not found",
        )
    return StudyMaterialJobResponse.model_validate(job)


@router.get(
    "/study-materials/{material_id}",
    response_model=StudyMaterialResponse,
)
def get_study_material_detail(
    material_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudyMaterialResponse:
    material = get_study_material(
        session,
        user_id=user.id,
        material_id=material_id,
    )
    if material is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Study material not found",
        )
    return material
