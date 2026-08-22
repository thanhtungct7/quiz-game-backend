"""Add difficulty level (EASY/MEDIUM/HARD) to challenges.

Revision ID: 20260822_0006
Revises: 20260822_0005
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260822_0006"
down_revision: str | None = "20260822_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

challenge_difficulty = sa.Enum("EASY", "MEDIUM", "HARD", name="challenge_difficulty")


def upgrade() -> None:
    challenge_difficulty.create(op.get_bind(), checkfirst=True)
    op.add_column(
        "challenges",
        sa.Column(
            "difficulty",
            challenge_difficulty,
            nullable=False,
            server_default="MEDIUM",
        ),
    )


def downgrade() -> None:
    op.drop_column("challenges", "difficulty")
    challenge_difficulty.drop(op.get_bind(), checkfirst=True)
