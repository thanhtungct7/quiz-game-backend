"""Add admin-only metadata columns to challenges (correct_text, tags,
cefr_level, toeic_band, toeic_min_score) so the source quiz data's full
metadata survives import instead of being dropped down to just
difficulty/explanation.

Revision ID: 20260823_0009
Revises: 20260823_0008
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0009"
down_revision: str | None = "20260823_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("challenges", sa.Column("correct_text", sa.Text(), nullable=True))
    op.add_column(
        "challenges", sa.Column("tags", sa.ARRAY(sa.String(length=50)), nullable=True)
    )
    op.add_column("challenges", sa.Column("cefr_level", sa.String(length=20), nullable=True))
    op.add_column("challenges", sa.Column("toeic_band", sa.String(length=20), nullable=True))
    op.add_column("challenges", sa.Column("toeic_min_score", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("challenges", "toeic_min_score")
    op.drop_column("challenges", "toeic_band")
    op.drop_column("challenges", "cefr_level")
    op.drop_column("challenges", "tags")
    op.drop_column("challenges", "correct_text")
