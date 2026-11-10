from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


DetailLevel = Literal["concise", "standard", "detailed"]
CitationKey = Annotated[str, Field(pattern=r"^P[0-9]{4}$")]
CLINICAL_SAFETY_NOTICE = (
    "AI-generated, unreviewed educational draft. Citations identify retrieved source "
    "passages only; they do not establish that a clinical statement is supported, "
    "accurate, current, or safe. Qualified nursing/clinical review is required before "
    "educational or clinical reliance."
)


class StudyNotesGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_ids: list[UUID] = Field(min_length=1, max_length=10)
    detail_level: DetailLevel = "standard"

    @field_validator("detail_level", mode="before")
    @classmethod
    def normalize_detail_level(cls, value: object) -> object:
        return value.strip().lower() if isinstance(value, str) else value

    @field_validator("document_ids")
    @classmethod
    def document_ids_are_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Document IDs must be unique")
        return value


class GeneratedNotePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    text: str = Field(min_length=1, max_length=1200)
    citation_keys: list[CitationKey] = Field(min_length=1, max_length=5)

    @field_validator("citation_keys")
    @classmethod
    def citation_keys_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Citation keys must be unique")
        return value


class GeneratedNoteSection(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    heading: str = Field(min_length=1, max_length=120)
    points: list[GeneratedNotePoint] = Field(min_length=1, max_length=10)


class GeneratedStudyNotes(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    overview: GeneratedNotePoint
    sections: list[GeneratedNoteSection] = Field(min_length=1, max_length=12)


class FlashcardGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    document_ids: list[UUID] = Field(min_length=1, max_length=10)
    card_count: int = Field(default=20, ge=1, le=30, strict=True)

    @field_validator("document_ids")
    @classmethod
    def document_ids_are_unique(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Document IDs must be unique")
        return value


class GeneratedFlashcard(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    front: str = Field(min_length=1, max_length=300)
    back: str = Field(min_length=1, max_length=1200)
    explanation: str | None = Field(default=None, max_length=600)
    difficulty: Literal["easy", "medium", "hard"] | None = None
    tags: list[str] = Field(default_factory=list, max_length=8)
    citation_keys: list[CitationKey] = Field(min_length=1, max_length=5)

    @field_validator("tags")
    @classmethod
    def tags_are_bounded_and_unique(cls, value: list[str]) -> list[str]:
        normalized = [tag.strip() for tag in value]
        if any(not tag or len(tag) > 32 for tag in normalized):
            raise ValueError("Flashcard tags are invalid")
        if len(normalized) != len(set(normalized)):
            raise ValueError("Flashcard tags must be unique")
        return normalized

    @field_validator("citation_keys")
    @classmethod
    def citation_keys_are_unique(cls, value: list[str]) -> list[str]:
        if len(value) != len(set(value)):
            raise ValueError("Citation keys must be unique")
        return value


class GeneratedFlashcards(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    title: str = Field(min_length=1, max_length=160)
    cards: list[GeneratedFlashcard] = Field(min_length=1, max_length=30)


FlashcardClaimField = Literal["front", "back", "explanation"]


class FlashcardClaimSupportCheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    card_position: int = Field(ge=0, le=29)
    field: FlashcardClaimField
    claim_index: int = Field(ge=0, le=299)
    supported: bool


class FlashcardGroundingAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    checks: list[FlashcardClaimSupportCheck] = Field(min_length=1, max_length=300)


class StudyMaterialJobResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    study_kit_id: UUID
    material_id: UUID | None = None
    kind: str
    status: str
    request_payload: dict[str, object]
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None


class StudyMaterialCitationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    citation_key: str
    document_id: UUID
    document_name: str
    generation_id: UUID
    chunk_index: int
    page_start: int
    page_end: int


class StudyMaterialCardResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    position: int
    front: str
    back: str
    explanation: str | None
    difficulty: Literal["easy", "medium", "hard"] | None
    tags: list[str]
    citation_keys: list[str]
    ai_generated: Literal[True] = True
    review_status: Literal["unreviewed"] = "unreviewed"


class StudyMaterialResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ai_generated: Literal[True] = True
    review_status: Literal["unreviewed"] = "unreviewed"
    safety_notice: str = CLINICAL_SAFETY_NOTICE
    id: UUID
    study_kit_id: UUID
    job_id: UUID
    kind: str
    schema_version: int
    version_number: int
    title: str
    content: dict[str, object]
    citations: list[StudyMaterialCitationResponse]
    cards: list[StudyMaterialCardResponse] = Field(default_factory=list)
    created_at: datetime


class FlashcardReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["remembered", "needs_review"]


class FlashcardReviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    card_id: UUID
    rating: Literal["remembered", "needs_review"]
    reviewed_at: datetime


class FlashcardProgressResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    material_id: UUID
    total_cards: int
    new_cards: int
    reviewed_cards: int
    remembered_cards: int
    needs_review_cards: int
    total_attempts: int
    latest_reviewed_at: datetime | None
