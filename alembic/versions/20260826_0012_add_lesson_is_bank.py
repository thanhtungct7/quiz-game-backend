"""Add lessons.is_bank so overflow question-bank lessons stay off the course tree.

Revision ID: 20260826_0012
Revises: 20260826_0011
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260826_0012"
down_revision: str | None = "20260826_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "lessons",
        sa.Column("is_bank", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_lessons_is_bank", "lessons", ["is_bank"])


def downgrade() -> None:
    op.drop_index("ix_lessons_is_bank", table_name="lessons")
    op.drop_column("lessons", "is_bank")
