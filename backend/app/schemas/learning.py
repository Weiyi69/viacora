from datetime import datetime
from typing import Generic, Literal, TypeVar
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

SubjectColor = Literal["blue", "violet", "teal", "green", "amber", "rose"]
SubjectIcon = Literal[
    "book",
    "flask",
    "calculator",
    "globe",
    "music",
    "code",
    "atom",
    "language",
    "history",
]
ActivityType = Literal[
    "subject_created",
    "study_space_created",
    "topic_created",
    "subject_accessed",
    "study_space_accessed",
]
AccessResourceType = Literal["subject", "study_space"]


def _trim_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    return cleaned or None


class LearningRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SubjectCreate(LearningRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    color: SubjectColor | None = None
    icon: SubjectIcon | None = None

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank")
        return cleaned

    _trim_description = field_validator("description")(_trim_optional_text)


class SubjectUpdate(LearningRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    color: SubjectColor | None = None
    icon: SubjectIcon | None = None

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank")
        return cleaned

    _trim_description = field_validator("description")(_trim_optional_text)

    @model_validator(mode="after")
    def require_update_fields(self) -> "SubjectUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Name cannot be null")
        return self


class StudySpaceCreate(LearningRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank")
        return cleaned

    _trim_description = field_validator("description")(_trim_optional_text)


class StudySpaceUpdate(LearningRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank")
        return cleaned

    _trim_description = field_validator("description")(_trim_optional_text)

    @model_validator(mode="after")
    def require_update_fields(self) -> "StudySpaceUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Name cannot be null")
        return self


class TopicCreate(LearningRequest):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    position: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank")
        return cleaned

    _trim_description = field_validator("description")(_trim_optional_text)


class TopicUpdate(LearningRequest):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=5000)
    position: int | None = Field(default=None, ge=0)

    @field_validator("name")
    @classmethod
    def trim_name(cls, value: str | None) -> str | None:
        if value is None:
            return None
        cleaned = value.strip()
        if not cleaned:
            raise ValueError("Name cannot be blank")
        return cleaned

    _trim_description = field_validator("description")(_trim_optional_text)

    @model_validator(mode="after")
    def require_update_fields(self) -> "TopicUpdate":
        if not self.model_fields_set:
            raise ValueError("At least one field must be provided")
        if "name" in self.model_fields_set and self.name is None:
            raise ValueError("Name cannot be null")
        if "position" in self.model_fields_set and self.position is None:
            raise ValueError("Position cannot be null")
        return self


class SubjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    description: str | None
    color: str | None
    icon: str | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    study_space_count: int = 0


class StudySpaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    subject_id: UUID
    name: str
    description: str | None
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None
    topic_count: int = 0


class TopicResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    study_space_id: UUID
    name: str
    description: str | None
    position: int
    created_at: datetime
    updated_at: datetime


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    event_type: ActivityType
    subject_id: UUID | None
    study_space_id: UUID | None
    topic_id: UUID | None
    resource_name: str
    occurred_at: datetime


ItemT = TypeVar("ItemT")


class PageResponse(BaseModel, Generic[ItemT]):
    items: list[ItemT]
    page: int
    page_size: int
    total: int


class DashboardResponse(BaseModel):
    display_name: str
    subject_count: int
    study_space_count: int
    subjects: list[SubjectResponse]
    recently_accessed: list[ActivityResponse]
    recent_activity: list[ActivityResponse]


class ActivityAccessRequest(LearningRequest):
    resource_type: AccessResourceType
    resource_id: UUID
