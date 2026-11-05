import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Topic(Base):
    __tablename__ = "topics"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_topics_name_nonblank"),
        CheckConstraint("position >= 0", name="ck_topics_position_nonnegative"),
        Index("ix_topics_study_space_position", "study_space_id", "position"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    study_space_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("study_spaces.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.current_timestamp(),
    )

    study_space: Mapped["StudySpace"] = relationship(back_populates="topics")
    learning_activities: Mapped[list["LearningActivity"]] = relationship(
        back_populates="topic",
        cascade="all, delete-orphan",
        foreign_keys="LearningActivity.topic_id",
    )
