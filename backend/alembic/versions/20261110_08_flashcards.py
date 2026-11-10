"""Add source-cited flashcards and append-only review events.

Downgrade removes flashcard jobs, decks, cards, and review history.

Revision ID: 20261110_08
Revises: 20261109_07
Create Date: 2026-11-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261110_08"
down_revision: Union[str, None] = "20261109_07"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_APPEND_ONLY_FUNCTION = """
CREATE FUNCTION prevent_flashcard_review_event_update()
RETURNS trigger AS $$
BEGIN
    IF TG_OP = 'DELETE' AND NOT EXISTS (
        SELECT 1 FROM study_material_cards WHERE id = OLD.card_id
    ) THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'flashcard review events are append-only'
        USING ERRCODE = 'check_violation';
END;
$$ LANGUAGE plpgsql
"""

_CARD_CITATION_GUARD_FUNCTION = """
CREATE FUNCTION require_flashcard_card_citation()
RETURNS trigger AS $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM study_material_cards
        WHERE material_id = NEW.material_id AND id = NEW.id
    ) AND NOT EXISTS (
        SELECT 1
        FROM study_material_card_citations
        WHERE material_id = NEW.material_id AND card_id = NEW.id
    ) THEN
        RAISE EXCEPTION 'every flashcard must have at least one citation'
            USING ERRCODE = 'check_violation';
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""

_CITATION_LINK_GUARD_FUNCTION = """
CREATE FUNCTION require_flashcard_citation_set_nonempty()
RETURNS trigger AS $$
DECLARE
    target_material_id uuid;
    target_card_id uuid;
BEGIN
    IF TG_OP <> 'INSERT' THEN
        target_material_id := OLD.material_id;
        target_card_id := OLD.card_id;
        IF EXISTS (
            SELECT 1
            FROM study_material_cards
            WHERE material_id = target_material_id AND id = target_card_id
        ) AND NOT EXISTS (
            SELECT 1
            FROM study_material_card_citations
            WHERE material_id = target_material_id AND card_id = target_card_id
        ) THEN
            RAISE EXCEPTION 'every flashcard must have at least one citation'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;

    IF TG_OP <> 'DELETE' THEN
        target_material_id := NEW.material_id;
        target_card_id := NEW.card_id;
        IF EXISTS (
            SELECT 1
            FROM study_material_cards
            WHERE material_id = target_material_id AND id = target_card_id
        ) AND NOT EXISTS (
            SELECT 1
            FROM study_material_card_citations
            WHERE material_id = target_material_id AND card_id = target_card_id
        ) THEN
            RAISE EXCEPTION 'every flashcard must have at least one citation'
                USING ERRCODE = 'check_violation';
        END IF;
    END IF;
    RETURN NULL;
END;
$$ LANGUAGE plpgsql
"""


def upgrade() -> None:
    with op.batch_alter_table("study_material_jobs") as batch_op:
        batch_op.drop_constraint("ck_study_material_jobs_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_study_material_jobs_kind", "kind IN ('notes', 'flashcards')"
        )
    with op.batch_alter_table("study_materials") as batch_op:
        batch_op.drop_constraint("ck_study_materials_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_study_materials_kind", "kind IN ('notes', 'flashcards')"
        )
    with op.batch_alter_table("study_materials") as batch_op:
        batch_op.create_unique_constraint(
            "uq_study_materials_user_id_id", ["user_id", "id"]
        )
        batch_op.create_unique_constraint(
            "uq_study_materials_id_kind", ["id", "kind"]
        )
    with op.batch_alter_table("study_material_citations") as batch_op:
        batch_op.create_unique_constraint(
            "uq_study_material_citations_material_id_id",
            ["study_material_id", "id"],
        )

    op.create_table(
        "study_material_cards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column(
            "material_kind",
            sa.String(length=16),
            server_default="flashcards",
            nullable=False,
        ),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("front", sa.Text(), nullable=False),
        sa.Column("back", sa.Text(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=True),
        sa.Column("difficulty", sa.String(length=16), nullable=True),
        sa.Column("tags", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint("position >= 0", name="ck_study_material_cards_position"),
        sa.CheckConstraint(
            "material_kind = 'flashcards'",
            name="ck_study_material_cards_material_kind",
        ),
        sa.CheckConstraint(
            "length(trim(front)) > 0 AND length(trim(back)) > 0",
            name="ck_study_material_cards_content_nonblank",
        ),
        sa.CheckConstraint(
            "difficulty IS NULL OR difficulty IN ('easy', 'medium', 'hard')",
            name="ck_study_material_cards_difficulty",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "material_id"],
            ["study_materials.user_id", "study_materials.id"],
            name="fk_study_material_cards_user_material",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_id", "material_kind"],
            ["study_materials.id", "study_materials.kind"],
            name="fk_study_material_cards_material_kind",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "material_id", "position",
            name="uq_study_material_cards_material_position",
        ),
        sa.UniqueConstraint(
            "material_id", "id", name="uq_study_material_cards_material_id_id"
        ),
        sa.UniqueConstraint("user_id", "id", name="uq_study_material_cards_user_id_id"),
    )
    op.create_table(
        "study_material_card_citations",
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("citation_id", sa.Uuid(), nullable=False),
        sa.ForeignKeyConstraint(
            ["material_id", "card_id"],
            ["study_material_cards.material_id", "study_material_cards.id"],
            name="fk_study_material_card_citations_material_card",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["material_id", "citation_id"],
            ["study_material_citations.study_material_id", "study_material_citations.id"],
            name="fk_study_material_card_citations_material_citation",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("material_id", "card_id", "citation_id"),
        sa.UniqueConstraint(
            "card_id",
            "citation_id",
            name="uq_study_material_card_citations_card_citation",
        ),
    )
    op.create_index(
        "ix_study_material_card_citations_material",
        "study_material_card_citations",
        ["material_id", "citation_id"],
    )
    op.create_table(
        "flashcard_review_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("card_id", sa.Uuid(), nullable=False),
        sa.Column("rating", sa.String(length=16), nullable=False),
        sa.Column("idempotency_key", sa.String(length=160), nullable=False),
        sa.Column(
            "reviewed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "rating IN ('remembered', 'needs_review')",
            name="ck_flashcard_review_events_rating",
        ),
        sa.ForeignKeyConstraint(
            ["user_id", "card_id"],
            ["study_material_cards.user_id", "study_material_cards.id"],
            name="fk_flashcard_review_events_user_card",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "idempotency_key",
            name="uq_flashcard_review_events_user_idempotency",
        ),
    )
    op.create_index(
        "ix_flashcard_review_events_user_card",
        "flashcard_review_events",
        ["user_id", "card_id"],
    )
    op.create_index(
        "ix_flashcard_review_events_card_reviewed",
        "flashcard_review_events",
        ["card_id", "reviewed_at"],
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(_APPEND_ONLY_FUNCTION)
        op.execute(
            """
            CREATE TRIGGER flashcard_review_events_no_update
            BEFORE UPDATE OR DELETE ON flashcard_review_events
            FOR EACH ROW EXECUTE FUNCTION prevent_flashcard_review_event_update()
            """
        )
        op.execute(_CARD_CITATION_GUARD_FUNCTION)
        op.execute(
            """
            CREATE CONSTRAINT TRIGGER flashcard_card_requires_citation
            AFTER INSERT ON study_material_cards
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION require_flashcard_card_citation()
            """
        )
        op.execute(_CITATION_LINK_GUARD_FUNCTION)
        op.execute(
            """
            CREATE CONSTRAINT TRIGGER flashcard_citation_set_remains_nonempty
            AFTER INSERT OR UPDATE OR DELETE ON study_material_card_citations
            DEFERRABLE INITIALLY DEFERRED
            FOR EACH ROW EXECUTE FUNCTION require_flashcard_citation_set_nonempty()
            """
        )


def downgrade() -> None:
    op.execute(
        "DELETE FROM study_material_jobs WHERE kind = 'flashcards'"
    )
    if op.get_bind().dialect.name == "postgresql":
        op.execute("SET CONSTRAINTS ALL IMMEDIATE")
        op.execute(
            "DROP TRIGGER IF EXISTS flashcard_citation_set_remains_nonempty "
            "ON study_material_card_citations"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS flashcard_card_requires_citation "
            "ON study_material_cards"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS require_flashcard_card_citation()"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS require_flashcard_citation_set_nonempty()"
        )
        op.execute(
            "DROP TRIGGER IF EXISTS flashcard_review_events_no_update "
            "ON flashcard_review_events"
        )
        op.execute(
            "DROP FUNCTION IF EXISTS prevent_flashcard_review_event_update()"
        )
    op.drop_index(
        "ix_flashcard_review_events_card_reviewed",
        table_name="flashcard_review_events",
    )
    op.drop_index(
        "ix_flashcard_review_events_user_card",
        table_name="flashcard_review_events",
    )
    op.drop_table("flashcard_review_events")
    op.drop_index(
        "ix_study_material_card_citations_material",
        table_name="study_material_card_citations",
    )
    op.drop_table("study_material_card_citations")
    op.drop_table("study_material_cards")
    with op.batch_alter_table("study_material_citations") as batch_op:
        batch_op.drop_constraint(
            "uq_study_material_citations_material_id_id", type_="unique"
        )
    with op.batch_alter_table("study_materials") as batch_op:
        batch_op.drop_constraint("uq_study_materials_user_id_id", type_="unique")
        batch_op.drop_constraint("uq_study_materials_id_kind", type_="unique")
    with op.batch_alter_table("study_materials") as batch_op:
        batch_op.drop_constraint("ck_study_materials_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_study_materials_kind", "kind IN ('notes')"
        )
    with op.batch_alter_table("study_material_jobs") as batch_op:
        batch_op.drop_constraint("ck_study_material_jobs_kind", type_="check")
        batch_op.create_check_constraint(
            "ck_study_material_jobs_kind", "kind IN ('notes')"
        )
