"""Add topics table and challenges.topic_id so questions can be filtered and
counted by topic independently of the course/unit/lesson hierarchy.

Revision ID: 20260823_0007
Revises: 20260822_0006
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260823_0007"
down_revision: str | None = "20260822_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "topics",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_topics_name"),
    )

    op.add_column("challenges", sa.Column("topic_id", sa.String(length=36), nullable=True))
    op.create_index(op.f("ix_challenges_topic_id"), "challenges", ["topic_id"], unique=False)
    op.create_foreign_key(
        "fk_challenges_topic_id_topics",
        "challenges",
        "topics",
        ["topic_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_challenges_topic_id_topics", "challenges", type_="foreignkey")
    op.drop_index(op.f("ix_challenges_topic_id"), table_name="challenges")
    op.drop_column("challenges", "topic_id")
    op.drop_table("topics")
