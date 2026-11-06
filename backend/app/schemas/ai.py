from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


class AIRetrievalRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    query: str = Field(min_length=1, max_length=2048)
    document_ids: list[UUID] | None = Field(default=None, max_length=100)
    top_k: int = Field(default=5, ge=1, le=8)

    @field_validator("document_ids")
    @classmethod
    def document_ids_are_unique(cls, value: list[UUID] | None) -> list[UUID] | None:
        if value is not None and len(value) != len(set(value)):
            raise ValueError("Document IDs must be unique")
        return value


class AISourceReference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    document_id: UUID
    document_name: str
    page_start: int
    page_end: int


class AIRetrievalPassage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    similarity: float = Field(ge=-1, le=1)
    source: AISourceReference


class AIRetrievalResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    passages: list[AIRetrievalPassage]


class AIUsageResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    period_start: datetime
    input_tokens: int
    output_tokens: int
    estimated_cost_usd: float
    event_count: int
