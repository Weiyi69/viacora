import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class Document(Base):
    __tablename__ = "documents"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_documents_status",
        ),
        CheckConstraint("attempts_used BETWEEN 0 AND 3", name="ck_documents_attempts_used"),
        CheckConstraint(
            "file_size_bytes BETWEEN 1 AND 26214400",
            name="ck_documents_file_size_bytes",
        ),
        CheckConstraint(
            "page_count IS NULL OR page_count BETWEEN 0 AND 500",
            name="ck_documents_page_count",
        ),
        ForeignKeyConstraint(
            ["user_id", "subject_id"],
            ["subjects.user_id", "subjects.id"],
            name="fk_documents_user_subject",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["subject_id", "study_space_id"],
            ["study_spaces.subject_id", "study_spaces.id"],
            name="fk_documents_subject_study_space",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["topic_id", "study_space_id"],
            ["topics.id", "topics.study_space_id"],
            name="fk_documents_topic_study_space",
        ),
        Index("ix_documents_user_created", "user_id", "created_at"),
        Index("ix_documents_user_status_created", "user_id", "status", "created_at"),
        Index("ix_documents_subject_created", "subject_id", "created_at"),
        Index("ix_documents_space_status_created", "study_space_id", "status", "created_at"),
        Index("ix_documents_topic", "topic_id"),
        Index("ix_documents_queue", "status", "next_attempt_at", "lease_expires_at"),
        UniqueConstraint(
            "user_id",
            "study_space_id",
            "id",
            name="uq_documents_user_space_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    subject_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    study_space_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    topic_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    mime_type: Mapped[str] = mapped_column(
        String(64), nullable=False, default="application/pdf", server_default="application/pdf"
    )
    storage_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    has_extractable_text: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    attempts_used: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    retryable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    page_count: Mapped[int | None] = mapped_column(Integer)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.current_timestamp(),
    )
    pages: Mapped[list["DocumentPage"]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DocumentPage.page_number",
    )
    index_generations: Mapped[list["DocumentIndexGeneration"]] = relationship(
        back_populates="document", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentPage(Base):
    __tablename__ = "document_pages"
    __table_args__ = (
        CheckConstraint("page_number BETWEEN 1 AND 500", name="ck_document_pages_page_number"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    page_number: Mapped[int] = mapped_column(Integer, primary_key=True)
    extracted_text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    document: Mapped[Document] = relationship(back_populates="pages")


class DocumentUploadAttempt(Base):
    __tablename__ = "document_upload_attempts"
    __table_args__ = (Index("ix_document_upload_attempts_user_time", "user_id", "attempted_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
