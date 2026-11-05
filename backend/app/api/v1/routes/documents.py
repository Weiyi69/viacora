from urllib.parse import quote
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session
from starlette.datastructures import UploadFile as StarletteUploadFile
from starlette.responses import FileResponse

from app.api.v1.dependencies import current_user, database_session, require_csrf
from app.core.config import get_settings
from app.models import User
from app.schemas.documents import (
    DocumentLibraryResponse,
    DocumentPageListResponse,
    DocumentPageResponse,
    DocumentResponse,
    DocumentStatus,
    DocumentTopicUpdate,
)
from app.services.documents import (
    assign_document_topic,
    check_upload_rate_limit,
    delete_document,
    get_document,
    get_document_file,
    list_document_pages,
    list_documents,
    retry_document,
    upload_document,
)

router = APIRouter(tags=["documents"])


@router.post(
    "/subjects/{subject_id}/study-spaces/{study_space_id}/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
async def post_document(
    subject_id: UUID,
    study_space_id: UUID,
    request: Request,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> DocumentResponse:
    check_upload_rate_limit(session, user_id=user.id)
    async with request.form(max_files=1, max_fields=2, max_part_size=256) as form:
        if set(form.keys()) - {"file", "topic_id"}:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Unexpected multipart field",
            )
        files = form.getlist("file")
        topic_values = form.getlist("topic_id")
        if len(files) != 1 or len(topic_values) > 1:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Exactly one PDF file and at most one topic are allowed",
            )
        upload = files[0]
        if not isinstance(upload, StarletteUploadFile):
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="A PDF file is required",
            )
        topic_id = None
        if topic_values:
            try:
                topic_id = UUID(str(topic_values[0]))
            except ValueError as error:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="Topic ID must be a UUID",
                ) from error
        document = await upload_document(
            session,
            user_id=user.id,
            subject_id=subject_id,
            study_space_id=study_space_id,
            topic_id=topic_id,
            upload=upload,
        )
    return DocumentResponse.model_validate(document)


@router.get("/documents", response_model=DocumentLibraryResponse)
def get_document_library(
    subject_id: UUID | None = None,
    study_space_id: UUID | None = None,
    topic_id: UUID | None = None,
    document_status: DocumentStatus | None = Query(default=None, alias="status"),
    q: str | None = Query(default=None, max_length=255),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> DocumentLibraryResponse:
    documents, total, used_bytes = list_documents(
        session,
        user_id=user.id,
        subject_id=subject_id,
        study_space_id=study_space_id,
        topic_id=topic_id,
        status_filter=document_status,
        query=q.strip() if q and q.strip() else None,
        page=page,
        page_size=page_size,
    )
    return DocumentLibraryResponse(
        items=[DocumentResponse.model_validate(document) for document in documents],
        page=page,
        page_size=page_size,
        total=total,
        storage_used_bytes=used_bytes,
        storage_quota_bytes=get_settings().document_storage_quota_bytes,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
def get_document_detail(
    document_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> DocumentResponse:
    return DocumentResponse.model_validate(
        get_document(session, user_id=user.id, document_id=document_id)
    )


@router.get("/documents/{document_id}/pages", response_model=DocumentPageListResponse)
def get_document_pages(
    document_id: UUID,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=50),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> DocumentPageListResponse:
    pages, total = list_document_pages(
        session,
        user_id=user.id,
        document_id=document_id,
        page=page,
        page_size=page_size,
    )
    return DocumentPageListResponse(
        items=[DocumentPageResponse.model_validate(item) for item in pages],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/documents/{document_id}/file")
def get_document_original(
    document_id: UUID,
    disposition: str = Query(default="attachment", pattern="^(attachment|inline)$"),
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> FileResponse:
    settings = get_settings()
    if disposition == "inline" and settings.environment == "production":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Inline preview is disabled in production",
        )
    path, filename = get_document_file(
        session, user_id=user.id, document_id=document_id
    )
    ascii_fallback = "".join(
        char for char in filename if char.isascii() and char.isprintable() and char not in {'"', "\\"}
    ) or "document.pdf"
    content_disposition = (
        f"{disposition}; filename=\"{ascii_fallback}\"; "
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    frame_policy = (
        f"frame-ancestors {settings.frontend_origin}"
        if disposition == "inline"
        else "frame-ancestors 'none'"
    )
    headers = {
        "Content-Disposition": content_disposition,
        "Cache-Control": "private, no-store",
        "Referrer-Policy": "no-referrer",
        "X-Content-Type-Options": "nosniff",
        "Cross-Origin-Resource-Policy": "same-site",
        "Content-Security-Policy": f"default-src 'none'; {frame_policy}",
    }
    if disposition == "attachment":
        headers["X-Frame-Options"] = "DENY"
    return FileResponse(path, media_type="application/pdf", headers=headers)


@router.patch(
    "/documents/{document_id}",
    response_model=DocumentResponse,
    dependencies=[Depends(require_csrf)],
)
def patch_document(
    document_id: UUID,
    payload: DocumentTopicUpdate,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> DocumentResponse:
    return DocumentResponse.model_validate(
        assign_document_topic(
            session,
            user_id=user.id,
            document_id=document_id,
            topic_id=payload.topic_id,
        )
    )


@router.post(
    "/documents/{document_id}/retry",
    response_model=DocumentResponse,
    dependencies=[Depends(require_csrf)],
)
def post_document_retry(
    document_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> DocumentResponse:
    return DocumentResponse.model_validate(
        retry_document(session, user_id=user.id, document_id=document_id)
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def remove_document(
    document_id: UUID,
    user: User = Depends(current_user),
    session: Session = Depends(database_session),
) -> Response:
    delete_document(session, user_id=user.id, document_id=document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
