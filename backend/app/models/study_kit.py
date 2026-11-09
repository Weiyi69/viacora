import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
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


class StudyKit(Base):
    __tablename__ = "study_kits"
    __table_args__ = (
        CheckConstraint("length(trim(title)) > 0", name="ck_study_kits_title_nonblank"),
        ForeignKeyConstraint(
            ["user_id", "subject_id"],
            ["subjects.user_id", "subjects.id"],
            name="fk_study_kits_user_subject",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["subject_id", "study_space_id"],
            ["study_spaces.subject_id", "study_spaces.id"],
            name="fk_study_kits_subject_study_space",
            ondelete="CASCADE",
        ),
        UniqueConstraint("user_id", "id", name="uq_study_kits_user_id_id"),
        UniqueConstraint(
            "user_id",
            "study_space_id",
            "id",
            name="uq_study_kits_user_space_id",
        ),
        Index("ix_study_kits_user_subject", "user_id", "subject_id"),
        Index("ix_study_kits_subject_space", "subject_id", "study_space_id"),
        Index("ix_study_kits_space_archived_updated", "study_space_id", "archived_at", "updated_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    study_space_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    title: Mapped[str] = mapped_column(String(120), nullable=False)
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

    study_space: Mapped["StudySpace"] = relationship(back_populates="study_kits")
    jobs: Mapped[list["StudyMaterialJob"]] = relationship(
        back_populates="study_kit",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    materials: Mapped[list["StudyMaterial"]] = relationship(
        back_populates="study_kit",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
