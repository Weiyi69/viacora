from datetime import datetime
from uuid import UUID

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.schemas.learning import LearningRequest, _trim_optional_text


class StudyKitCreate(LearningRequest):
    title: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Title cannot be blank")
        return cleaned

    _trim_description = field_validator("description")(_trim_optional_text)


class StudyKitUpdate(LearningRequest):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("title")
    @classmethod
    def trim_title(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Title cannot be blank")
        return cleaned

    _trim_description = field_validator("description")(_trim_optional_text)

    @model_validator(mode="after")
    def require_update_fields(self) -> "StudyKitUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "title" in self.model_fields_set and self.title is None:
            raise ValueError("Title cannot be null")
        return self


class StudyKitResponse(LearningRequest):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_id: UUID
    study_space_id: UUID
    title: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
