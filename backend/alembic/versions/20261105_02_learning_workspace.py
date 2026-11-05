"""Create learning workspace tables.

Revision ID: 20261105_02
Revises: 20261105_01
Create Date: 2026-11-05
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261105_02"
down_revision: Union[str, None] = "20261105_01"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "subjects",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("color", sa.String(length=24), nullable=True),
        sa.Column("icon", sa.String(length=32), nullable=True),
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
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_subjects_name_nonblank"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_subjects_user_archived_updated",
        "subjects",
        ["user_id", "archived_at", "updated_at"],
    )
    op.create_table(
        "study_spaces",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
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
        sa.CheckConstraint(
            "length(trim(name)) > 0", name="ck_study_spaces_name_nonblank"
        ),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_study_spaces_subject_archived_updated",
        "study_spaces",
        ["subject_id", "archived_at", "updated_at"],
    )
    op.create_table(
        "topics",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("study_space_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("position", sa.Integer(), server_default="0", nullable=False),
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
        sa.CheckConstraint("length(trim(name)) > 0", name="ck_topics_name_nonblank"),
        sa.CheckConstraint("position >= 0", name="ck_topics_position_nonnegative"),
        sa.ForeignKeyConstraint(
            ["study_space_id"], ["study_spaces.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_topics_study_space_position", "topics", ["study_space_id", "position"]
    )
    op.create_table(
        "learning_activities",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(length=32), nullable=False),
        sa.Column("subject_id", sa.Uuid(), nullable=True),
        sa.Column("study_space_id", sa.Uuid(), nullable=True),
        sa.Column("topic_id", sa.Uuid(), nullable=True),
        sa.Column("resource_name", sa.String(length=120), nullable=False),
        sa.Column(
            "occurred_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "event_type IN ('subject_created', 'study_space_created', 'topic_created', "
            "'subject_accessed', 'study_space_accessed')",
            name="ck_learning_activities_event_type",
        ),
        sa.CheckConstraint(
            "(event_type IN ('subject_created', 'subject_accessed') "
            "AND subject_id IS NOT NULL AND study_space_id IS NULL AND topic_id IS NULL) "
            "OR (event_type IN ('study_space_created', 'study_space_accessed') "
            "AND subject_id IS NULL AND study_space_id IS NOT NULL AND topic_id IS NULL) "
            "OR (event_type = 'topic_created' "
            "AND subject_id IS NULL AND study_space_id IS NULL AND topic_id IS NOT NULL)",
            name="ck_learning_activities_resource_matches_event",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["subject_id"], ["subjects.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["study_space_id"], ["study_spaces.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_learning_activities_user_occurred",
        "learning_activities",
        ["user_id", "occurred_at"],
    )
    op.create_index("ix_learning_activities_subject", "learning_activities", ["subject_id"])
    op.create_index(
        "ix_learning_activities_study_space", "learning_activities", ["study_space_id"]
    )
    op.create_index("ix_learning_activities_topic", "learning_activities", ["topic_id"])


def downgrade() -> None:
    op.drop_index("ix_learning_activities_topic", table_name="learning_activities")
    op.drop_index(
        "ix_learning_activities_study_space", table_name="learning_activities"
    )
    op.drop_index("ix_learning_activities_subject", table_name="learning_activities")
    op.drop_index(
        "ix_learning_activities_user_occurred", table_name="learning_activities"
    )
    op.drop_table("learning_activities")
    op.drop_index("ix_topics_study_space_position", table_name="topics")
    op.drop_table("topics")
    op.drop_index(
        "ix_study_spaces_subject_archived_updated", table_name="study_spaces"
    )
    op.drop_table("study_spaces")
    op.drop_index("ix_subjects_user_archived_updated", table_name="subjects")
    op.drop_table("subjects")
