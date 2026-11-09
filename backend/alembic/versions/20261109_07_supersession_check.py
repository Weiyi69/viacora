"""Require complete, same-kit study material supersession references.

Revision ID: 20261109_07
Revises: 20261109_06
Create Date: 2026-11-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261109_07"
down_revision: Union[str, None] = "20261109_06"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_CONSTRAINT_NAME = "ck_study_materials_supersedes_same_kit"
_CHECK_EXPRESSION = (
    "(supersedes_material_id IS NULL AND supersedes_study_kit_id IS NULL) "
    "OR (supersedes_material_id IS NOT NULL "
    "AND supersedes_study_kit_id IS NOT NULL "
    "AND supersedes_study_kit_id = study_kit_id)"
)
_LEGACY_INVALID_ROWS = sa.text(
    "SELECT count(*) "
    "FROM study_materials "
    "WHERE (supersedes_material_id IS NULL AND supersedes_study_kit_id IS NOT NULL) "
    "OR (supersedes_material_id IS NOT NULL "
    "AND (supersedes_study_kit_id IS NULL "
    "OR supersedes_study_kit_id <> study_kit_id))"
)


def upgrade() -> None:
    invalid_count = op.get_bind().execute(_LEGACY_INVALID_ROWS).scalar_one()
    if invalid_count:
        raise RuntimeError(
            "Cannot apply 20261109_07: "
            f"{invalid_count} study_materials row(s) have incomplete or cross-kit "
            "supersession references. Repair these rows before retrying."
        )

    with op.batch_alter_table("study_materials") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT_NAME, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT_NAME, _CHECK_EXPRESSION)


def downgrade() -> None:
    legacy_check = (
        "(supersedes_material_id IS NULL AND supersedes_study_kit_id IS NULL) "
        "OR (supersedes_material_id IS NOT NULL "
        "AND supersedes_study_kit_id = study_kit_id)"
    )
    with op.batch_alter_table("study_materials") as batch_op:
        batch_op.drop_constraint(_CONSTRAINT_NAME, type_="check")
        batch_op.create_check_constraint(_CONSTRAINT_NAME, legacy_check)
