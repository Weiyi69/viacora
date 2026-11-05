from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.learning import PageResponse

DocumentStatus = Literal["queued", "processing", "completed", "failed"]


class DocumentTopicUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    topic_id: UUID | None = Field(...)


class DocumentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_id: UUID
    study_space_id: UUID
    topic_id: UUID | None
    original_filename: str
    file_size_bytes: int
    status: DocumentStatus
    attempts_used: int
    retryable: bool
    error_code: str | None
    page_count: int | None
    has_extractable_text: bool
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None


class DocumentLibraryResponse(BaseModel):
    items: list[DocumentResponse]
    page: int
    page_size: int
    total: int
    storage_used_bytes: int
    storage_quota_bytes: int


class DocumentPageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    page_number: int
    extracted_text: str


class DocumentPageListResponse(PageResponse[DocumentPageResponse]):
    pass
