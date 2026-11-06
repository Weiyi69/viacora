import uuid
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.types import TypeEngine

from app.models.base import Base

if TYPE_CHECKING:
    from app.models.document import Document

_VECTOR_1536: TypeEngine[object] = Vector(1536).with_variant(JSON(), "sqlite")


class AIEmbeddingProfile(Base):
    __tablename__ = "ai_embedding_profiles"
    __table_args__ = (
        CheckConstraint("singleton_key IS TRUE", name="ck_ai_embedding_profiles_singleton"),
        CheckConstraint("dimensions = 1536", name="ck_ai_embedding_profiles_dimensions"),
        CheckConstraint("distance_metric = 'cosine'", name="ck_ai_embedding_profiles_distance"),
    )

    singleton_key: Mapped[bool] = mapped_column(Boolean, primary_key=True, default=True)
    profile_id: Mapped[str] = mapped_column(String(100), nullable=False, unique=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_metric: Mapped[str] = mapped_column(String(16), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class DocumentIndexGeneration(Base):
    __tablename__ = "document_index_generations"
    __table_args__ = (
        CheckConstraint("generation_number > 0", name="ck_document_index_generations_number"),
        CheckConstraint("dimensions = 1536", name="ck_document_index_generations_dimensions"),
        CheckConstraint(
            "status IN ('building', 'ready', 'failed')",
            name="ck_document_index_generations_status",
        ),
        ForeignKeyConstraint(
            ["embedding_profile_id"],
            ["ai_embedding_profiles.profile_id"],
            name="fk_document_index_generations_profile",
        ),
        UniqueConstraint(
            "document_id", "generation_number", name="uq_document_index_generations_number"
        ),
        UniqueConstraint("document_id", "id", name="uq_document_index_generations_doc_id"),
        UniqueConstraint(
            "id", "embedding_profile_id", name="uq_document_index_generations_id_profile"
        ),
        UniqueConstraint(
            "document_id",
            "embedding_profile_id",
            "id",
            name="uq_document_index_generations_doc_profile_id",
        ),
        UniqueConstraint(
            "document_id",
            "source_document_sha256",
            "embedding_profile_id",
            "chunking_version",
            name="uq_document_index_generations_source_profile_chunking",
        ),
        Index(
            "ix_document_index_generations_document_status",
            "document_id",
            "status",
            "generation_number",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    generation_number: Mapped[int] = mapped_column(Integer, nullable=False)
    source_document_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    embedding_profile_id: Mapped[str] = mapped_column(String(100), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    dimensions: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_metric: Mapped[str] = mapped_column(String(16), nullable=False)
    chunking_version: Mapped[str] = mapped_column(String(64), nullable=False)
    token_count_strategy: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="building", server_default="building"
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    ready_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    document: Mapped["Document"] = relationship(back_populates="index_generations")
    chunks: Mapped[list["DocumentChunk"]] = relationship(
        back_populates="generation", cascade="all, delete-orphan", passive_deletes=True
    )


class DocumentIndexHead(Base):
    __tablename__ = "document_index_heads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "profile_id", "generation_id"],
            [
                "document_index_generations.document_id",
                "document_index_generations.embedding_profile_id",
                "document_index_generations.id",
            ],
            name="fk_document_index_heads_generation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["profile_id"],
            ["ai_embedding_profiles.profile_id"],
            name="fk_document_index_heads_profile",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(100), nullable=False)
    generation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("CURRENT_TIMESTAMP"),
        onupdate=func.current_timestamp(),
    )
    generation: Mapped[DocumentIndexGeneration] = relationship()


class DocumentIndexJob(Base):
    __tablename__ = "document_index_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_document_index_jobs_status",
        ),
        CheckConstraint("attempts_used BETWEEN 0 AND 5", name="ck_document_index_jobs_attempts"),
        ForeignKeyConstraint(
            ["document_id", "generation_id"],
            ["document_index_generations.document_id", "document_index_generations.id"],
            name="fk_document_index_jobs_generation",
            ondelete="CASCADE",
        ),
        UniqueConstraint("generation_id", name="uq_document_index_jobs_generation"),
        Index(
            "ix_document_index_jobs_queue",
            "status",
            "next_attempt_at",
            "lease_expires_at",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    generation_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="queued", server_default="queued"
    )
    attempts_used: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    next_attempt_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DocumentChunk(Base):
    __tablename__ = "document_chunks"
    __table_args__ = (
        CheckConstraint("chunk_index >= 0", name="ck_document_chunks_index"),
        CheckConstraint("page_start BETWEEN 1 AND 500", name="ck_document_chunks_page_start"),
        CheckConstraint(
            "page_end BETWEEN page_start AND 500", name="ck_document_chunks_page_end"
        ),
        CheckConstraint(
            "utf8_byte_upper_bound BETWEEN 1 AND 1024",
            name="ck_document_chunks_byte_bound",
        ),
        ForeignKeyConstraint(
            ["document_id", "generation_id"],
            ["document_index_generations.document_id", "document_index_generations.id"],
            name="fk_document_chunks_generation",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "page_start"],
            ["document_pages.document_id", "document_pages.page_number"],
            name="fk_document_chunks_page_start",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["document_id", "page_end"],
            ["document_pages.document_id", "document_pages.page_number"],
            name="fk_document_chunks_page_end",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "document_id", "generation_id", "chunk_index", name="uq_document_chunks_doc_gen_index"
        ),
        Index("ix_document_chunks_page", "document_id", "page_start", "page_end"),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    generation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    page_start: Mapped[int] = mapped_column(Integer, nullable=False)
    page_end: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    utf8_byte_upper_bound: Mapped[int] = mapped_column(Integer, nullable=False)
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    generation: Mapped[DocumentIndexGeneration] = relationship(back_populates="chunks")
    embedding: Mapped["DocumentChunkEmbedding | None"] = relationship(
        back_populates="chunk", cascade="all, delete-orphan", passive_deletes=True, uselist=False
    )


class DocumentChunkEmbedding(Base):
    __tablename__ = "document_chunk_embeddings"
    __table_args__ = (
        ForeignKeyConstraint(
            ["document_id", "generation_id", "chunk_index"],
            ["document_chunks.document_id", "document_chunks.generation_id", "document_chunks.chunk_index"],
            name="fk_document_chunk_embeddings_chunk",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["generation_id", "profile_id"],
            ["document_index_generations.id", "document_index_generations.embedding_profile_id"],
            name="fk_document_chunk_embeddings_generation_profile",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["profile_id"],
            ["ai_embedding_profiles.profile_id"],
            name="fk_document_chunk_embeddings_profile",
        ),
        Index(
            "ix_document_chunk_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    generation_id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    chunk_index: Mapped[int] = mapped_column(Integer, primary_key=True)
    profile_id: Mapped[str] = mapped_column(String(100), nullable=False)
    embedding: Mapped[list[float]] = mapped_column(_VECTOR_1536, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    chunk: Mapped[DocumentChunk] = relationship(back_populates="embedding")


class AIUsageEvent(Base):
    __tablename__ = "ai_usage_events"
    __table_args__ = (
        CheckConstraint("input_tokens >= 0", name="ck_ai_usage_events_input_tokens"),
        CheckConstraint("output_tokens >= 0", name="ck_ai_usage_events_output_tokens"),
        CheckConstraint("estimated_cost_usd >= 0", name="ck_ai_usage_events_cost"),
        CheckConstraint(
            "accounting_basis IN ('provider_reported', 'conservative_estimate')",
            name="ck_ai_usage_events_accounting_basis",
        ),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        ForeignKeyConstraint(
            ["reservation_id"],
            ["ai_usage_reservations.id"],
            name="fk_ai_usage_events_reservation",
            ondelete="SET NULL",
        ),
        UniqueConstraint("reservation_id", name="uq_ai_usage_events_reservation"),
        UniqueConstraint("idempotency_key", name="uq_ai_usage_events_idempotency"),
        Index("ix_ai_usage_events_user_time", "user_id", "occurred_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reservation_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(100), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(64), nullable=False)
    accounting_basis: Mapped[str] = mapped_column(String(32), nullable=False)
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )


class AIUsageReservation(Base):
    __tablename__ = "ai_usage_reservations"
    __table_args__ = (
        CheckConstraint(
            "status IN ('reserved', 'dispatching', 'uncertain', 'reconciled', 'released', 'estimated')",
            name="ck_ai_usage_reservations_status",
        ),
        CheckConstraint("reserved_input_tokens >= 0", name="ck_ai_usage_reservations_tokens"),
        CheckConstraint(
            "reserved_output_tokens >= 0",
            name="ck_ai_usage_reservations_output_tokens",
        ),
        CheckConstraint("reserved_cost_usd > 0", name="ck_ai_usage_reservations_cost"),
        ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        UniqueConstraint("user_id", "idempotency_key", name="uq_ai_usage_reservations_user_key"),
        Index("ix_ai_usage_reservations_user_status_expiry", "user_id", "status", "expires_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    document_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(160), nullable=False)
    reserved_input_tokens: Mapped[int] = mapped_column(Integer, nullable=False)
    reserved_output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reserved_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 8), nullable=False)
    pricing_version: Mapped[str] = mapped_column(String(64), nullable=False)
    model_name: Mapped[str] = mapped_column(String(100), nullable=False)
    profile_id: Mapped[str] = mapped_column(String(100), nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default="reserved", server_default="reserved"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("CURRENT_TIMESTAMP")
    )
    dispatch_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finalized_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    provider_request_id: Mapped[str | None] = mapped_column(String(128))
