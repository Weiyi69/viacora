"""Constrain study material provenance to its owner, space, and source set.

Revision ID: 20261109_06
Revises: 20261109_05
Create Date: 2026-11-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_06"
down_revision: Union[str, None] = "20261109_05"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("documents") as batch_op:
        batch_op.create_unique_constraint(
            "uq_documents_user_space_id",
            ["user_id", "study_space_id", "id"],
        )

    op.add_column(
        "study_material_jobs",
        sa.Column("study_space_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE study_material_jobs AS job "
            "SET study_space_id = kit.study_space_id "
            "FROM study_kits AS kit "
            "WHERE kit.id = job.study_kit_id AND kit.user_id = job.user_id"
        )
    )
    with op.batch_alter_table("study_material_jobs") as batch_op:
        batch_op.alter_column(
            "study_space_id",
            existing_type=sa.Uuid(),
            nullable=False,
        )
        batch_op.drop_constraint("fk_study_material_jobs_user_kit", type_="foreignkey")
        batch_op.create_foreign_key(
            "fk_study_material_jobs_user_space_kit",
            "study_kits",
            ["user_id", "study_space_id", "study_kit_id"],
            ["user_id", "study_space_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_unique_constraint(
            "uq_study_material_jobs_user_space_id",
            ["user_id", "study_space_id", "id"],
        )

    op.add_column(
        "study_material_job_sources",
        sa.Column("user_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "study_material_job_sources",
        sa.Column("study_space_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE study_material_job_sources AS source "
            "SET user_id = job.user_id, study_space_id = job.study_space_id "
            "FROM study_material_jobs AS job "
            "WHERE job.id = source.job_id"
        )
    )
    with op.batch_alter_table("study_material_job_sources") as batch_op:
        batch_op.alter_column("user_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.alter_column("study_space_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.drop_constraint(
            "fk_study_material_job_sources_job",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_study_material_job_sources_document",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_study_material_job_sources_user_space_job",
            "study_material_jobs",
            ["user_id", "study_space_id", "job_id"],
            ["user_id", "study_space_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_study_material_job_sources_user_space_document",
            "documents",
            ["user_id", "study_space_id", "document_id"],
            ["user_id", "study_space_id", "id"],
            ondelete="RESTRICT",
        )
        batch_op.create_unique_constraint(
            "uq_study_material_job_sources_job_doc_generation",
            ["job_id", "document_id", "generation_id"],
        )

    with op.batch_alter_table("study_materials") as batch_op:
        batch_op.create_unique_constraint(
            "uq_study_materials_id_job",
            ["id", "job_id"],
        )
        batch_op.create_unique_constraint(
            "uq_study_materials_kit_id_id",
            ["study_kit_id", "id"],
        )
    op.add_column(
        "study_materials",
        sa.Column("supersedes_study_kit_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE study_materials AS material "
            "SET supersedes_study_kit_id = predecessor.study_kit_id "
            "FROM study_materials AS predecessor "
            "WHERE predecessor.id = material.supersedes_material_id"
        )
    )
    with op.batch_alter_table("study_materials") as batch_op:
        batch_op.drop_constraint(
            "fk_study_materials_supersedes",
            type_="foreignkey",
        )
        batch_op.create_check_constraint(
            "ck_study_materials_supersedes_same_kit",
            "(supersedes_material_id IS NULL AND supersedes_study_kit_id IS NULL) "
            "OR (supersedes_material_id IS NOT NULL "
            "AND supersedes_study_kit_id = study_kit_id)",
        )
        batch_op.create_foreign_key(
            "fk_study_materials_supersedes",
            "study_materials",
            ["supersedes_study_kit_id", "supersedes_material_id"],
            ["study_kit_id", "id"],
            ondelete="SET NULL",
        )

    op.add_column(
        "study_material_citations",
        sa.Column("job_id", sa.Uuid(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE study_material_citations AS citation "
            "SET job_id = material.job_id "
            "FROM study_materials AS material "
            "WHERE material.id = citation.study_material_id"
        )
    )
    with op.batch_alter_table("study_material_citations") as batch_op:
        batch_op.alter_column("job_id", existing_type=sa.Uuid(), nullable=False)
        batch_op.drop_constraint(
            "fk_study_material_citations_material",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_study_material_citations_material_job",
            "study_materials",
            ["study_material_id", "job_id"],
            ["id", "job_id"],
            ondelete="CASCADE",
        )
        batch_op.create_foreign_key(
            "fk_study_material_citations_job_source",
            "study_material_job_sources",
            ["job_id", "document_id", "generation_id"],
            ["job_id", "document_id", "generation_id"],
            ondelete="CASCADE",
        )
    op.create_index(
        "ix_study_kits_user_subject",
        "study_kits",
        ["user_id", "subject_id"],
    )
    op.create_index(
        "ix_study_kits_subject_space",
        "study_kits",
        ["subject_id", "study_space_id"],
    )
    op.create_index(
        "ix_study_material_jobs_user_space_kit",
        "study_material_jobs",
        ["user_id", "study_space_id", "study_kit_id"],
    )
    op.create_index(
        "ix_study_material_job_sources_user_space_job",
        "study_material_job_sources",
        ["user_id", "study_space_id", "job_id"],
    )
    op.create_index(
        "ix_study_material_job_sources_user_space_document",
        "study_material_job_sources",
        ["user_id", "study_space_id", "document_id"],
    )
    op.create_index(
        "ix_study_materials_user_kit",
        "study_materials",
        ["user_id", "study_kit_id"],
    )
    op.create_index(
        "ix_study_materials_user_kit_job",
        "study_materials",
        ["user_id", "study_kit_id", "job_id"],
    )
    op.create_index(
        "ix_study_materials_supersedes",
        "study_materials",
        ["supersedes_study_kit_id", "supersedes_material_id"],
    )
    op.create_index(
        "ix_study_material_citations_material_job",
        "study_material_citations",
        ["study_material_id", "job_id"],
    )
    op.create_index(
        "ix_study_material_citations_job_source",
        "study_material_citations",
        ["job_id", "document_id", "generation_id"],
    )
    op.create_index(
        "ix_study_material_citations_chunk",
        "study_material_citations",
        ["document_id", "generation_id", "chunk_index"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_study_material_citations_chunk",
        table_name="study_material_citations",
    )
    op.drop_index(
        "ix_study_material_citations_job_source",
        table_name="study_material_citations",
    )
    op.drop_index(
        "ix_study_material_citations_material_job",
        table_name="study_material_citations",
    )
    op.drop_index("ix_study_materials_supersedes", table_name="study_materials")
    op.drop_index("ix_study_materials_user_kit_job", table_name="study_materials")
    op.drop_index("ix_study_materials_user_kit", table_name="study_materials")
    op.drop_index(
        "ix_study_material_job_sources_user_space_document",
        table_name="study_material_job_sources",
    )
    op.drop_index(
        "ix_study_material_job_sources_user_space_job",
        table_name="study_material_job_sources",
    )
    op.drop_index(
        "ix_study_material_jobs_user_space_kit",
        table_name="study_material_jobs",
    )
    op.drop_index("ix_study_kits_subject_space", table_name="study_kits")
    op.drop_index("ix_study_kits_user_subject", table_name="study_kits")

    with op.batch_alter_table("study_material_citations") as batch_op:
        batch_op.drop_constraint(
            "fk_study_material_citations_job_source",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_study_material_citations_material_job",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_study_material_citations_material",
            "study_materials",
            ["study_material_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("job_id")

    with op.batch_alter_table("study_materials") as batch_op:
        batch_op.drop_constraint(
            "fk_study_materials_supersedes",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "ck_study_materials_supersedes_same_kit",
            type_="check",
        )
        batch_op.create_foreign_key(
            "fk_study_materials_supersedes",
            "study_materials",
            ["supersedes_material_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.drop_column("supersedes_study_kit_id")
        batch_op.drop_constraint(
            "uq_study_materials_kit_id_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "uq_study_materials_id_job",
            type_="unique",
        )

    with op.batch_alter_table("study_material_job_sources") as batch_op:
        batch_op.drop_constraint(
            "uq_study_material_job_sources_job_doc_generation",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_study_material_job_sources_user_space_document",
            type_="foreignkey",
        )
        batch_op.drop_constraint(
            "fk_study_material_job_sources_user_space_job",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_study_material_job_sources_document",
            "documents",
            ["document_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_foreign_key(
            "fk_study_material_job_sources_job",
            "study_material_jobs",
            ["job_id"],
            ["id"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("study_space_id")
        batch_op.drop_column("user_id")

    with op.batch_alter_table("study_material_jobs") as batch_op:
        batch_op.drop_constraint(
            "uq_study_material_jobs_user_space_id",
            type_="unique",
        )
        batch_op.drop_constraint(
            "fk_study_material_jobs_user_space_kit",
            type_="foreignkey",
        )
        batch_op.create_foreign_key(
            "fk_study_material_jobs_user_kit",
            "study_kits",
            ["user_id", "study_kit_id"],
            ["user_id", "id"],
            ondelete="CASCADE",
        )
        batch_op.drop_column("study_space_id")

    with op.batch_alter_table("documents") as batch_op:
        batch_op.drop_constraint(
            "uq_documents_user_space_id",
            type_="unique",
        )
