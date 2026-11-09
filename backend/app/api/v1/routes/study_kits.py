from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import current_user, database_session, require_csrf
from app.models import User
from app.schemas.learning import PageResponse
from app.schemas.study_kits import StudyKitCreate, StudyKitResponse, StudyKitUpdate
from app.services.study_kits import (
    archive_study_kit,
    create_study_kit,
    delete_study_kit,
    get_study_kit,
    list_study_kits,
    restore_study_kit,
    update_study_kit,
)

router = APIRouter(tags=["study kits"])


@router.get(
    "/study-spaces/{study_space_id}/study-kits",
    response_model=PageResponse[StudyKitResponse],
)
def get_study_space_kits(
    study_space_id: UUID,
    include_archived: bool = False,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> PageResponse[StudyKitResponse]:
    kits, total = list_study_kits(
        session,
        user_id=user.id,
        study_space_id=study_space_id,
        include_archived=include_archived,
        page=page,
        page_size=page_size,
    )
    return PageResponse[StudyKitResponse](
        items=[StudyKitResponse.model_validate(kit) for kit in kits],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/study-spaces/{study_space_id}/study-kits",
    response_model=StudyKitResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def post_study_kit(
    study_space_id: UUID,
    payload: StudyKitCreate,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudyKitResponse:
    kit = create_study_kit(
        session,
        user_id=user.id,
        study_space_id=study_space_id,
        payload=payload,
    )
    return StudyKitResponse.model_validate(kit)


@router.get("/study-kits/{study_kit_id}", response_model=StudyKitResponse)
def get_study_kit_detail(
    study_kit_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudyKitResponse:
    kit = get_study_kit(session, user_id=user.id, study_kit_id=study_kit_id)
    return StudyKitResponse.model_validate(kit)


@router.patch(
    "/study-kits/{study_kit_id}",
    response_model=StudyKitResponse,
    dependencies=[Depends(require_csrf)],
)
def patch_study_kit(
    study_kit_id: UUID,
    payload: StudyKitUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudyKitResponse:
    kit = update_study_kit(
        session,
        user_id=user.id,
        study_kit_id=study_kit_id,
        payload=payload,
    )
    return StudyKitResponse.model_validate(kit)


@router.post(
    "/study-kits/{study_kit_id}/archive",
    response_model=StudyKitResponse,
    dependencies=[Depends(require_csrf)],
)
def post_archive_study_kit(
    study_kit_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudyKitResponse:
    kit = archive_study_kit(
        session, user_id=user.id, study_kit_id=study_kit_id
    )
    return StudyKitResponse.model_validate(kit)


@router.post(
    "/study-kits/{study_kit_id}/restore",
    response_model=StudyKitResponse,
    dependencies=[Depends(require_csrf)],
)
def post_restore_study_kit(
    study_kit_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> StudyKitResponse:
    kit = restore_study_kit(
        session, user_id=user.id, study_kit_id=study_kit_id
    )
    return StudyKitResponse.model_validate(kit)


@router.delete(
    "/study-kits/{study_kit_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def remove_study_kit(
    study_kit_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> Response:
    delete_study_kit(session, user_id=user.id, study_kit_id=study_kit_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
