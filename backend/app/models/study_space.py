import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class StudySpace(Base):
    __tablename__ = "study_spaces"
    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_study_spaces_name_nonblank"),
        Index(
            "ix_study_spaces_subject_archived_updated",
            "subject_id",
            "archived_at",
            "updated_at",
        ),
        UniqueConstraint("subject_id", "id", name="uq_study_spaces_subject_id_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    subject_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("subjects.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.current_timestamp(),
    )
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    subject: Mapped["Subject"] = relationship(back_populates="study_spaces")
    topics: Mapped[list["Topic"]] = relationship(
        back_populates="study_space", cascade="all, delete-orphan"
    )
    study_kits: Mapped[list["StudyKit"]] = relationship(
        back_populates="study_space",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    learning_activities: Mapped[list["LearningActivity"]] = relationship(
        back_populates="study_space",
        cascade="all, delete-orphan",
        foreign_keys="LearningActivity.study_space_id",
    )
