"""Add study kits and foundational study material tables.

Revision ID: 20261109_05
Revises: 20261106_04
Create Date: 2026-11-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_05"
down_revision: Union[str, None] = "20261106_04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "study_kits",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("study_space_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("archived_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("length(trim(title)) > 0", name="ck_study_kits_title_nonblank"),
        sa.ForeignKeyConstraint(
            ["user_id", "subject_id"],
            ["subjects.user_id", "subjects.id"],
            name="fk_study_kits_user_subject",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id", "study_space_id"],
            ["study_spaces.subject_id", "study_spaces.id"],
            name="fk_study_kits_subject_study_space",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "id", name="uq_study_kits_user_id_id"),
        sa.UniqueConstraint(
            "user_id",
            "study_space_id",
            "id",
            name="uq_study_kits_user_space_id",
        ),
    )
    op.create_index(
        "ix_study_kits_space_archived_updated",
        "study_kits",
        ["study_space_id", "archived_at", "updated_at"],
    )

    op.create_table(
        "study_material_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("study_kit_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="notes", nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("attempts_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancel_requested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("request_payload", sa.JSON(), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("kind IN ('notes')", name="ck_study_material_jobs_kind"),
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed', 'cancelled')",
            name="ck_study_material_jobs_status",
        ),
        sa.CheckConstraint(
            "attempts_used BETWEEN 0 AND 5",
            name="ck_study_material_jobs_attempts",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "study_kit_id"],
            ["study_kits.user_id", "study_kits.id"],
            name="fk_study_material_jobs_user_kit",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_study_material_jobs_user_idempotency",
        ),
        sa.UniqueConstraint(
            "user_id",
            "study_kit_id",
            "id",
            name="uq_study_material_jobs_user_kit_id",
        ),
    )
    op.create_index(
        "ix_study_material_jobs_queue",
        "study_material_jobs",
        ["status", "next_attempt_at", "lease_expires_at", "created_at"],
    )

    op.create_table(
        "study_material_job_sources",
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("source_document_sha256", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["study_material_jobs.id"],
            name="fk_study_material_job_sources_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id"],
            ["documents.id"],
            name="fk_study_material_job_sources_document",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "generation_id"],
            ["document_index_generations.document_id", "document_index_generations.id"],
            name="fk_study_material_job_sources_generation",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("job_id", "document_id"),
    )
    op.create_index(
        "ix_study_material_job_sources_document",
        "study_material_job_sources",
        ["document_id", "job_id"],
    )

    op.create_table(
        "study_materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("study_kit_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(length=16), server_default="notes", nullable=False),
        sa.Column("schema_version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("version_number", sa.Integer(), nullable=False),
        sa.Column("title", sa.String(length=160), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("supersedes_material_id", sa.Uuid(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("kind IN ('notes')", name="ck_study_materials_kind"),
        sa.CheckConstraint(
            "schema_version > 0",
            name="ck_study_materials_schema_version",
        ),
        sa.CheckConstraint(
            "version_number > 0",
            name="ck_study_materials_version",
        ),
        sa.CheckConstraint(
            "length(trim(title)) > 0",
            name="ck_study_materials_title_nonblank",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "study_kit_id"],
            ["study_kits.user_id", "study_kits.id"],
            name="fk_study_materials_user_kit",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "study_kit_id", "job_id"],
            [
                "study_material_jobs.user_id",
                "study_material_jobs.study_kit_id",
                "study_material_jobs.id",
            ],
            name="fk_study_materials_job",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_material_id"],
            ["study_materials.id"],
            name="fk_study_materials_supersedes",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", name="uq_study_materials_job"),
        sa.UniqueConstraint(
            "study_kit_id",
            "kind",
            "version_number",
            name="uq_study_materials_kit_kind_version",
        ),
    )

    op.create_table(
        "study_material_citations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("study_material_id", sa.Uuid(), nullable=False),
        sa.Column("citation_key", sa.String(length=64), nullable=False),
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("generation_id", sa.Uuid(), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["study_material_id"],
            ["study_materials.id"],
            name="fk_study_material_citations_material",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["document_id", "generation_id", "chunk_index"],
            [
                "document_chunks.document_id",
                "document_chunks.generation_id",
                "document_chunks.chunk_index",
            ],
            name="fk_study_material_citations_chunk",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "study_material_id",
            "citation_key",
            name="uq_study_material_citations_material_key",
        ),
    )
    op.create_index(
        "ix_study_material_citations_document",
        "study_material_citations",
        ["document_id", "study_material_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_study_material_citations_document", table_name="study_material_citations")
    op.drop_table("study_material_citations")
    op.drop_table("study_materials")
    op.drop_index(
        "ix_study_material_job_sources_document",
        table_name="study_material_job_sources",
    )
    op.drop_table("study_material_job_sources")
    op.drop_index("ix_study_material_jobs_queue", table_name="study_material_jobs")
    op.drop_table("study_material_jobs")
    op.drop_index("ix_study_kits_space_archived_updated", table_name="study_kits")
    op.drop_table("study_kits")
