from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy.orm import Session

from app.api.v1.dependencies import current_user, database_session, require_csrf
from app.models import User
from app.schemas.learning import PageResponse, TopicCreate, TopicResponse, TopicUpdate
from app.services.topics import (
    create_topic,
    delete_topic,
    get_topic,
    list_topics,
    update_topic,
)

router = APIRouter(tags=["topics"])


@router.get("/study-spaces/{study_space_id}/topics", response_model=PageResponse[TopicResponse])
def get_study_space_topics(
    study_space_id: UUID,
    q: str | None = Query(default=None, max_length=120),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=100),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> PageResponse[TopicResponse]:
    topics, total = list_topics(
        session,
        user_id=user.id,
        study_space_id=study_space_id,
        query=q.strip() if q and q.strip() else None,
        page=page,
        page_size=page_size,
    )
    return PageResponse[TopicResponse](
        items=[TopicResponse.model_validate(topic) for topic in topics],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.post(
    "/study-spaces/{study_space_id}/topics",
    response_model=TopicResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def post_topic(
    study_space_id: UUID,
    payload: TopicCreate,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> TopicResponse:
    topic = create_topic(
        session,
        user_id=user.id,
        study_space_id=study_space_id,
        payload=payload,
    )
    return TopicResponse.model_validate(topic)


@router.get("/topics/{topic_id}", response_model=TopicResponse)
def get_topic_detail(
    topic_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> TopicResponse:
    return TopicResponse.model_validate(
        get_topic(session, user_id=user.id, topic_id=topic_id)
    )


@router.patch(
    "/topics/{topic_id}",
    response_model=TopicResponse,
    dependencies=[Depends(require_csrf)],
)
def patch_topic(
    topic_id: UUID,
    payload: TopicUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> TopicResponse:
    topic = update_topic(
        session, user_id=user.id, topic_id=topic_id, payload=payload
    )
    return TopicResponse.model_validate(topic)


@router.delete(
    "/topics/{topic_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def remove_topic(
    topic_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> Response:
    delete_topic(session, user_id=user.id, topic_id=topic_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
