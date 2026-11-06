"""Add versioned AI document indexes and usage accounting.

Revision ID: 20261106_04
Revises: 20261106_03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy import text

revision: str = "20261106_04"
down_revision: Union[str, None] = "20261106_03"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _create_extension_if_available() -> None:
    bind = op.get_bind()
    available = bind.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector')")
    )
    if not available:
        raise RuntimeError(
            "The PostgreSQL vector extension package is unavailable; install pgvector before "
            "applying this migration."
        )
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


def upgrade() -> None:
    is_postgresql = op.get_bind().dialect.name == "postgresql"
    if is_postgresql:
        _create_extension_if_available()
    embedding_type = Vector(1536) if is_postgresql else sa.JSON()

    op.create_table(
        "ai_embedding_profiles",
        sa.Column("singleton_key", sa.Boolean(), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=False),
        sa.Column("provider", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("distance_metric", sa.String(length=16), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("singleton_key IS TRUE", name="ck_ai_embedding_profiles_singleton"),
        sa.CheckConstraint("dimensions = 1536", name="ck_ai_embedding_profiles_dimensions"),
        sa.CheckConstraint(
            "distance_metric = 'cosine'", name="ck_ai_embedding_profiles_distance"
        ),
        sa.PrimaryKeyConstraint("singleton_key"),
        sa.UniqueConstraint("profile_id", name="uq_ai_embedding_profiles_profile_id"),
    )
    profile_table = sa.table(
        "ai_embedding_profiles",
        sa.column("singleton_key", sa.Boolean()),
        sa.column("profile_id", sa.String()),
        sa.column("provider", sa.String()),
        sa.column("model_name", sa.String()),
        sa.column("dimensions", sa.Integer()),
        sa.column("distance_metric", sa.String()),
    )
    op.bulk_insert(
        profile_table,
        [
            {
                "singleton_key": True,
                "profile_id": "openai-text-embedding-3-small-1536-v1",
                "provider": "openai",
                "model_name": "text-embedding-3-small",
                "dimensions": 1536,
                "distance_metric": "cosine",
            }
        ],
    )

    op.create_table(
        "document_index_generations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("generation_number", sa.Integer(), nullable=False),
        sa.Column("source_document_sha256", sa.String(length=64), nullable=False),
        sa.Column("embedding_profile_id", sa.String(length=100), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("distance_metric", sa.String(length=16), nullable=False),
        sa.Column("chunking_version", sa.String(length=64), nullable=False),
        sa.Column("token_count_strategy", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="building", nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("ready_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "generation_number > 0", name="ck_document_index_generations_number"
        ),
        sa.CheckConstraint("dimensions = 1536", name="ck_document_index_generations_dimensions"),
        sa.CheckConstraint(
            "status IN ('building', 'ready', 'failed')",
            name="ck_document_index_generations_status",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"], ["documents.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(
            ["embedding_profile_id"],
            ["ai_embedding_profiles.profile_id"],
            name="fk_document_index_generations_profile",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "document_id", "generation_number", name="uq_document_index_generations_number"
        ),
        sa.UniqueConstraint("document_id", "id", name="uq_document_index_generations_doc_id"),
        sa.UniqueConstraint(
            "id", "embedding_profile_id", name="uq_document_index_generations_id_profile"
        ),
        sa.UniqueConstraint(
            "document_id",
            "embedding_profile_id",
            "id",
            name="uq_document_index_generations_doc_profile_id",
        ),
        sa.UniqueConstraint(
            "document_id",
            "source_document_sha256",
            "embedding_profile_id",
            "chunking_version",
            name="uq_document_index_generations_source_profile_chunking",
        ),
    )
    op.create_index(
        "ix_document_index_generations_document_status",
        "document_index_generations",
        ["document_id", "status", "generation_number"],
    )

    op.create_table(
        "document_index_heads",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "profile_id", "generation_id"],
            [
                "document_index_generations.document_id",
                "document_index_generations.embedding_profile_id",
                "document_index_generations.id",
            ],
            name="fk_document_index_heads_generation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["ai_embedding_profiles.profile_id"],
            name="fk_document_index_heads_profile",
        ),
        sa.PrimaryKeyConstraint("document_id"),
    )

    op.create_table(
        "document_index_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("attempts_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_document_index_jobs_status",
        ),
        sa.CheckConstraint(
            "attempts_used BETWEEN 0 AND 5", name="ck_document_index_jobs_attempts"
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "generation_id"],
            ["document_index_generations.document_id", "document_index_generations.id"],
            name="fk_document_index_jobs_generation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("generation_id", name="uq_document_index_jobs_generation"),
    )
    op.create_index(
        "ix_document_index_jobs_queue",
        "document_index_jobs",
        ["status", "next_attempt_at", "lease_expires_at", "created_at"],
    )

    op.create_table(
        "document_chunks",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("page_start", sa.Integer(), nullable=False),
        sa.Column("page_end", sa.Integer(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("utf8_byte_upper_bound", sa.Integer(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.CheckConstraint("chunk_index >= 0", name="ck_document_chunks_index"),
        sa.CheckConstraint("page_start BETWEEN 1 AND 500", name="ck_document_chunks_page_start"),
        sa.CheckConstraint(
            "page_end BETWEEN page_start AND 500", name="ck_document_chunks_page_end"
        ),
        sa.CheckConstraint(
            "utf8_byte_upper_bound BETWEEN 1 AND 1024",
            name="ck_document_chunks_byte_bound",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "generation_id"],
            ["document_index_generations.document_id", "document_index_generations.id"],
            name="fk_document_chunks_generation",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "page_start"],
            ["document_pages.document_id", "document_pages.page_number"],
            name="fk_document_chunks_page_start",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "page_end"],
            ["document_pages.document_id", "document_pages.page_number"],
            name="fk_document_chunks_page_end",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("generation_id", "chunk_index"),
        sa.UniqueConstraint(
            "document_id", "generation_id", "chunk_index", name="uq_document_chunks_doc_gen_index"
        ),
    )
    op.create_index(
        "ix_document_chunks_page", "document_chunks", ["document_id", "page_start", "page_end"]
    )

    op.create_table(
        "document_chunk_embeddings",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=False),
        sa.Column("embedding", embedding_type, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "generation_id", "chunk_index"],
            [
                "document_chunks.document_id",
                "document_chunks.generation_id",
                "document_chunks.chunk_index",
            ],
            name="fk_document_chunk_embeddings_chunk",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["generation_id", "profile_id"],
            ["document_index_generations.id", "document_index_generations.embedding_profile_id"],
            name="fk_document_chunk_embeddings_generation_profile",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["profile_id"],
            ["ai_embedding_profiles.profile_id"],
            name="fk_document_chunk_embeddings_profile",
        ),
        sa.PrimaryKeyConstraint("generation_id", "chunk_index"),
    )
    if is_postgresql:
        op.execute(
            "CREATE INDEX ix_document_chunk_embeddings_hnsw "
            "ON document_chunk_embeddings USING hnsw (embedding vector_cosine_ops)"
        )

    op.create_table(
        "ai_usage_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("reserved_input_tokens", sa.Integer(), nullable=False),
        sa.Column("reserved_output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved_cost_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("pricing_version", sa.String(length=64), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="reserved", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("dispatch_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finalized_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
        sa.CheckConstraint(
            "status IN ('reserved', 'dispatching', 'uncertain', 'reconciled', 'released', 'estimated')",
            name="ck_ai_usage_reservations_status",
        ),
        sa.CheckConstraint(
            "reserved_input_tokens >= 0", name="ck_ai_usage_reservations_tokens"
        ),
        sa.CheckConstraint(
            "reserved_output_tokens >= 0", name="ck_ai_usage_reservations_output_tokens"
        ),
        sa.CheckConstraint("reserved_cost_usd > 0", name="ck_ai_usage_reservations_cost"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "idempotency_key", name="uq_ai_usage_reservations_user_key"
        ),
    )
    op.create_index(
        "ix_ai_usage_reservations_user_status_expiry",
        "ai_usage_reservations",
        ["user_id", "status", "expires_at"],
    )

    op.create_table(
        "ai_usage_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=True),
        sa.Column("reservation_id", sa.Uuid(), nullable=True),
        sa.Column("operation", sa.String(length=32), nullable=False),
        sa.Column("model_name", sa.String(length=100), nullable=False),
        sa.Column("profile_id", sa.String(length=100), nullable=False),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False),
        sa.Column("estimated_cost_usd", sa.Numeric(12, 8), nullable=False),
        sa.Column("pricing_version", sa.String(length=64), nullable=False),
        sa.Column("accounting_basis", sa.String(length=32), nullable=False),
        sa.Column("provider_request_id", sa.String(length=128), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("input_tokens >= 0", name="ck_ai_usage_events_input_tokens"),
        sa.CheckConstraint("output_tokens >= 0", name="ck_ai_usage_events_output_tokens"),
        sa.CheckConstraint("estimated_cost_usd >= 0", name="ck_ai_usage_events_cost"),
        sa.CheckConstraint(
            "accounting_basis IN ('provider_reported', 'conservative_estimate')",
            name="ck_ai_usage_events_accounting_basis",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["reservation_id"],
            ["ai_usage_reservations.id"],
            name="fk_ai_usage_events_reservation",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("reservation_id", name="uq_ai_usage_events_reservation"),
        sa.UniqueConstraint("idempotency_key", name="uq_ai_usage_events_idempotency"),
    )
    op.create_index("ix_ai_usage_events_user_time", "ai_usage_events", ["user_id", "occurred_at"])


def downgrade() -> None:
    op.drop_index("ix_ai_usage_events_user_time", table_name="ai_usage_events")
    op.drop_table("ai_usage_events")
    op.drop_index("ix_ai_usage_reservations_user_status_expiry", table_name="ai_usage_reservations")
    op.drop_table("ai_usage_reservations")
    if op.get_bind().dialect.name == "postgresql":
        op.drop_index(
            "ix_document_chunk_embeddings_hnsw", table_name="document_chunk_embeddings"
        )
    op.drop_table("document_chunk_embeddings")
    op.drop_index("ix_document_chunks_page", table_name="document_chunks")
    op.drop_table("document_chunks")
    op.drop_table("document_index_jobs")
    op.drop_table("document_index_heads")
    op.drop_index(
        "ix_document_index_generations_document_status",
        table_name="document_index_generations",
    )
    op.drop_table("document_index_generations")
    op.drop_table("ai_embedding_profiles")
