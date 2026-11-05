"""Add private learning documents and processing state.

Revision ID: 20261106_03
Revises: 20261105_02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261106_03"
down_revision: Union[str, None] = "20261105_02"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("subjects") as batch:
        batch.create_unique_constraint(
            "uq_subjects_user_id_id", ["user_id", "id"]
        )
    with op.batch_alter_table("study_spaces") as batch:
        batch.create_unique_constraint(
            "uq_study_spaces_subject_id_id", ["subject_id", "id"]
        )
    with op.batch_alter_table("topics") as batch:
        batch.create_unique_constraint(
            "uq_topics_id_study_space_id", ["id", "study_space_id"]
        )

    op.create_table(
        "documents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("study_space_id", sa.Uuid(), nullable=False),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column(
            "mime_type",
            sa.String(length=64),
            server_default="application/pdf",
            nullable=False,
        ),
        sa.Column("storage_key", sa.String(length=64), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("file_size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column(
            "has_extractable_text",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("attempts_used", sa.Integer(), server_default="0", nullable=False),
        sa.Column("retryable", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.CheckConstraint(
            "status IN ('queued', 'processing', 'completed', 'failed')",
            name="ck_documents_status",
        ),
        sa.CheckConstraint("attempts_used BETWEEN 0 AND 3", name="ck_documents_attempts_used"),
        sa.CheckConstraint(
            "file_size_bytes BETWEEN 1 AND 26214400",
            name="ck_documents_file_size_bytes",
        ),
        sa.CheckConstraint(
            "page_count IS NULL OR page_count BETWEEN 0 AND 500",
            name="ck_documents_page_count",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["user_id", "subject_id"],
            ["subjects.user_id", "subjects.id"],
            name="fk_documents_user_subject",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["subject_id", "study_space_id"],
            ["study_spaces.subject_id", "study_spaces.id"],
            name="fk_documents_subject_study_space",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["topic_id", "study_space_id"],
            ["topics.id", "topics.study_space_id"],
            name="fk_documents_topic_study_space",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("storage_key", name="uq_documents_storage_key"),
    )
    op.create_index("ix_documents_user_created", "documents", ["user_id", "created_at"])
    op.create_index(
        "ix_documents_user_status_created",
        "documents",
        ["user_id", "status", "created_at"],
    )
    op.create_index(
        "ix_documents_subject_created", "documents", ["subject_id", "created_at"]
    )
    op.create_index(
        "ix_documents_space_status_created",
        "documents",
        ["study_space_id", "status", "created_at"],
    )
    op.create_index("ix_documents_topic", "documents", ["topic_id"])
    op.create_index(
        "ix_documents_queue",
        "documents",
        ["status", "next_attempt_at", "lease_expires_at"],
    )

    op.create_table(
        "document_pages",
        sa.Column("document_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("extracted_text", sa.Text(), nullable=False),
        sa.CheckConstraint(
            "page_number BETWEEN 1 AND 500", name="ck_document_pages_page_number"
        ),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("document_id", "page_number"),
    )
    op.create_table(
        "document_upload_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_document_upload_attempts_user_time",
        "document_upload_attempts",
        ["user_id", "attempted_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_document_upload_attempts_user_time", table_name="document_upload_attempts")
    op.drop_table("document_upload_attempts")
    op.drop_table("document_pages")
    op.drop_index("ix_documents_queue", table_name="documents")
    op.drop_index("ix_documents_topic", table_name="documents")
    op.drop_index("ix_documents_space_status_created", table_name="documents")
    op.drop_index("ix_documents_subject_created", table_name="documents")
    op.drop_index("ix_documents_user_status_created", table_name="documents")
    op.drop_index("ix_documents_user_created", table_name="documents")
    op.drop_table("documents")
    with op.batch_alter_table("topics") as batch:
        batch.drop_constraint("uq_topics_id_study_space_id", type_="unique")
    with op.batch_alter_table("study_spaces") as batch:
        batch.drop_constraint("uq_study_spaces_subject_id_id", type_="unique")
    with op.batch_alter_table("subjects") as batch:
        batch.drop_constraint("uq_subjects_user_id_id", type_="unique")
