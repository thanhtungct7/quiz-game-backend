"""Add benchmark_cleared_level to user_game_profiles.

The last of the three chốt chặn năng lực: `services/game/cefr.LEVEL_CAPS`
(10, 25, 50, 75, 92) sit one below each CEFR band floor, and
`services/game/leveling.effective_level` holds a player's recognized level at
the lowest cap in that list they have not cleared yet. This column is the one
piece of state that moves a cap -- raised by passing the Benchmark Exam bound
to it -- and it is monotonic, so a fresh column defaulting to 0 is simply "no
Benchmark Exam passed yet" until a player clears their first one.

Revision ID: 20260910_0025
Revises: 20260910_0024
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260910_0025"
down_revision: str | None = "20260910_0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_game_profiles",
        sa.Column(
            "benchmark_cleared_level", sa.Integer(), nullable=False, server_default="0"
        ),
    )


def downgrade() -> None:
    op.drop_column("user_game_profiles", "benchmark_cleared_level")
