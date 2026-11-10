import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    JSON,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base


class StudyMaterialJob(Base):
    __tablename__ = "study_material_jobs"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('notes', 'flashcards')",
            name="ck_study_material_jobs_kind",
        ),
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')",
            name="ck_study_material_jobs_status",
        ),
        CheckConstraint("attempts_used BETWEEN 0 AND 5", name="ck_study_material_jobs_attempts"),
        ForeignKeyConstraint(
            ["user_id", "study_space_id", "study_kit_id"],
            ["study_kits.user_id", "study_kits.study_space_id", "study_kits.id"],
            name="fk_study_material_jobs_user_space_kit",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_study_material_jobs_user_idempotency",
        ),
        UniqueConstraint(
            "user_id",
            "study_kit_id",
            "id",
            name="uq_study_material_jobs_user_kit_id",
        ),
        UniqueConstraint(
            "user_id",
            "study_space_id",
            "id",
            name="uq_study_material_jobs_user_space_id",
        ),
        Index(
            "ix_study_material_jobs_queue",
            "status",
            "next_attempt_at",
            "lease_expires_at",
            "created_at",
        ),
        Index(
            "ix_study_material_jobs_user_space_kit",
            "user_id",
            "study_space_id",
            "study_kit_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    study_space_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    study_kit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="notes")
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    attempts_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancel_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    request_payload: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    study_kit: Mapped["StudyKit"] = relationship(back_populates="jobs")
    sources: Mapped[list["StudyMaterialJobSource"]] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    material: Mapped["StudyMaterial | None"] = relationship(
        back_populates="job",
        cascade="all, delete-orphan",
        passive_deletes=True,
        uselist=False,
        overlaps="materials,study_kit",
    )

    @property
    def material_id(self) -> uuid.UUID | None:
        return self.material.id if self.material is not None else None


class StudyMaterialJobSource(Base):
    __tablename__ = "study_material_job_sources"
    __table_args__ = (
        ForeignKeyConstraint(
            ["user_id", "study_space_id", "job_id"],
            [
                "study_material_jobs.user_id",
                "study_material_jobs.study_space_id",
                "study_material_jobs.id",
            ],
            name="fk_study_material_job_sources_user_space_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_id", "study_space_id", "document_id"],
            ["documents.user_id", "documents.study_space_id", "documents.id"],
            name="fk_study_material_job_sources_user_space_document",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["document_id", "generation_id"],
            ["document_index_generations.document_id", "document_index_generations.id"],
            name="fk_study_material_job_sources_generation",
            ondelete="RESTRICT",
        ),
        Index("ix_study_material_job_sources_document", "document_id", "job_id"),
        Index(
            "ix_study_material_job_sources_user_space_job",
            "user_id",
            "study_space_id",
            "job_id",
        ),
        Index(
            "ix_study_material_job_sources_user_space_document",
            "user_id",
            "study_space_id",
            "document_id",
        ),
        UniqueConstraint(
            "job_id",
            "document_id",
            "generation_id",
            name="uq_study_material_job_sources_job_doc_generation",
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    study_space_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    generation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    job: Mapped[StudyMaterialJob] = relationship(back_populates="sources")


class StudyMaterial(Base):
    __tablename__ = "study_materials"
    __table_args__ = (
        CheckConstraint("kind IN ('notes', 'flashcards')", name="ck_study_materials_kind"),
        CheckConstraint("schema_version > 0", name="ck_study_materials_schema_version"),
        CheckConstraint("version_number > 0", name="ck_study_materials_version"),
        CheckConstraint("length(trim(title)) > 0", name="ck_study_materials_title_nonblank"),
        ForeignKeyConstraint(
            ["user_id", "study_kit_id"],
            ["study_kits.user_id", "study_kits.id"],
            name="fk_study_materials_user_kit",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["user_id", "study_kit_id", "job_id"],
            ["study_material_jobs.user_id", "study_material_jobs.study_kit_id", "study_material_jobs.id"],
            name="fk_study_materials_job",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["supersedes_study_kit_id", "supersedes_material_id"],
            ["study_materials.study_kit_id", "study_materials.id"],
            name="fk_study_materials_supersedes",
            ondelete="SET NULL",
        ),
        CheckConstraint(
            "(supersedes_material_id IS NULL AND supersedes_study_kit_id IS NULL) "
            "OR (supersedes_material_id IS NOT NULL "
            "AND supersedes_study_kit_id IS NOT NULL "
            "AND supersedes_study_kit_id = study_kit_id)",
            name="ck_study_materials_supersedes_same_kit",
        ),
        UniqueConstraint("job_id", name="uq_study_materials_job"),
        UniqueConstraint("id", "job_id", name="uq_study_materials_id_job"),
        UniqueConstraint("id", "kind", name="uq_study_materials_id_kind"),
        UniqueConstraint("user_id", "id", name="uq_study_materials_user_id_id"),
        UniqueConstraint("study_kit_id", "id", name="uq_study_materials_kit_id_id"),
        Index("ix_study_materials_user_kit", "user_id", "study_kit_id"),
        Index(
            "ix_study_materials_user_kit_job",
            "user_id",
            "study_kit_id",
            "job_id",
        ),
        Index(
            "ix_study_materials_supersedes",
            "supersedes_study_kit_id",
            "supersedes_material_id",
        ),
        UniqueConstraint(
            "study_kit_id",
            "kind",
            "version_number",
            name="uq_study_materials_kit_kind_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    study_kit_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    kind: Mapped[str] = mapped_column(
        String(16), nullable=False, default="notes", server_default="notes"
    )
    schema_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(160), nullable=False)
    content: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    supersedes_material_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    supersedes_study_kit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )

    study_kit: Mapped["StudyKit"] = relationship(
        back_populates="materials",
        overlaps="material",
    )
    job: Mapped[StudyMaterialJob] = relationship(
        back_populates="material",
        overlaps="materials,study_kit",
    )
    citations: Mapped[list["StudyMaterialCitation"]] = relationship(
        back_populates="material",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )
    cards: Mapped[list["StudyMaterialCard"]] = relationship(
        back_populates="material",
        foreign_keys="[StudyMaterialCard.user_id, StudyMaterialCard.material_id]",
        cascade="all, delete-orphan",
        passive_deletes=True,
    )


class StudyMaterialCitation(Base):
    __tablename__ = "study_material_citations"
    __table_args__ = (
        ForeignKeyConstraint(
            ["job_id", "document_id", "generation_id"],
            [
                "study_material_job_sources.job_id",
                "study_material_job_sources.document_id",
                "study_material_job_sources.generation_id",
            ],
            name="fk_study_material_citations_job_source",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "generation_id", "chunk_index"],
            [
                "document_chunks.document_id",
                "document_chunks.generation_id",
                "document_chunks.chunk_index",
            ],
            name="fk_study_material_citations_chunk",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "study_material_id",
            "citation_key",
            name="uq_study_material_citations_material_key",
        ),
        UniqueConstraint(
            "study_material_id",
            "id",
            name="uq_study_material_citations_material_id_id",
        ),
        Index("ix_study_material_citations_document", "document_id", "study_material_id"),
        Index(
            "ix_study_material_citations_material_job",
            "study_material_id",
            "job_id",
        ),
        Index(
            "ix_study_material_citations_job_source",
            "job_id",
            "document_id",
            "generation_id",
        ),
        Index(
            "ix_study_material_citations_chunk",
            "document_id",
            "generation_id",
            "chunk_index",
        ),
        ForeignKeyConstraint(
            ["study_material_id", "job_id"],
            ["study_materials.id", "study_materials.job_id"],
            name="fk_study_material_citations_material_job",
            ondelete="CASCADE",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    study_material_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    job_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    citation_key: Mapped[str] = mapped_column(String(64), nullable=False)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    generation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)

    material: Mapped[StudyMaterial] = relationship(back_populates="citations")
