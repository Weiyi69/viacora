import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base

ACTIVITY_RESOURCE_RULE = (
    "(event_type IN ('subject_created', 'subject_accessed') "
    "AND subject_id IS NOT NULL AND study_space_id IS NULL AND topic_id IS NULL) "
    "OR (event_type IN ('study_space_created', 'study_space_accessed') "
    "AND subject_id IS NULL AND study_space_id IS NOT NULL AND topic_id IS NULL) "
    "OR (event_type = 'topic_created' "
    "AND subject_id IS NULL AND study_space_id IS NULL AND topic_id IS NOT NULL)"
)


class LearningActivity(Base):
    __tablename__ = "learning_activities"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('subject_created', 'study_space_created', 'topic_created', "
            "'subject_accessed', 'study_space_accessed')",
            name="ck_learning_activities_event_type",
        ),
        CheckConstraint(
            ACTIVITY_RESOURCE_RULE,
            name="ck_learning_activities_resource_matches_event",
        ),
        Index("ix_learning_activities_user_occurred", "user_id", "occurred_at"),
        Index("ix_learning_activities_subject", "subject_id"),
        Index("ix_learning_activities_study_space", "study_space_id"),
        Index("ix_learning_activities_topic", "topic_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(String(32), nullable=False)
    subject_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("subjects.id", ondelete="CASCADE")
    )
    study_space_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("study_spaces.id", ondelete="CASCADE")
    )
    topic_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("topics.id", ondelete="CASCADE")
    )
    resource_name: Mapped[str] = mapped_column(String(120), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    user: Mapped["User"] = relationship(back_populates="learning_activities")
    subject: Mapped["Subject | None"] = relationship(
        back_populates="learning_activities", foreign_keys=[subject_id]
    )
    study_space: Mapped["StudySpace | None"] = relationship(
        back_populates="learning_activities", foreign_keys=[study_space_id]
    )
    topic: Mapped["Topic | None"] = relationship(
        back_populates="learning_activities", foreign_keys=[topic_id]
    )
