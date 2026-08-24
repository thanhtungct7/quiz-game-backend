"""Add passages table plus challenges.passage_id/source_ref so reading
comprehension questions can share one passage row instead of duplicating
the article text on every question.

Revision ID: 20260823_0008
Revises: 20260823_0007
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0008"
down_revision: str | None = "20260823_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "passages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("source_ref", sa.String(length=64), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("level_grade", sa.String(length=20), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source_ref", name="uq_passages_source_ref"),
    )

    op.add_column("challenges", sa.Column("passage_id", sa.String(length=36), nullable=True))
    op.add_column("challenges", sa.Column("source_ref", sa.String(length=64), nullable=True))
    op.create_index(op.f("ix_challenges_passage_id"), "challenges", ["passage_id"], unique=False)
    op.create_index(op.f("ix_challenges_source_ref"), "challenges", ["source_ref"], unique=False)
    op.create_foreign_key(
        "fk_challenges_passage_id_passages",
        "challenges",
        "passages",
        ["passage_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_challenges_passage_id_passages", "challenges", type_="foreignkey")
    op.drop_index(op.f("ix_challenges_source_ref"), table_name="challenges")
    op.drop_index(op.f("ix_challenges_passage_id"), table_name="challenges")
    op.drop_column("challenges", "source_ref")
    op.drop_column("challenges", "passage_id")
    op.drop_table("passages")
