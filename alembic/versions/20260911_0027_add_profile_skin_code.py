"""Add skin_code to user_game_profiles: which cosmetic the player is wearing.

A pointer, not an inventory: the skins a player owns already live in
`user_items`, and this names the one of them currently on show. NULL means
none, and the client falls back to the CEFR band's colour -- see
`heroGlowFor` on the app side.

Shaped after `class_code` on the same table rather than a slot in
`user_equipment`: the code is a plain string with no join, and the player card
that reads it is fetched once per leaderboard row, so keeping it on the
profile row already in hand is what keeps that path at one query.

Revision ID: 20260911_0027
Revises: 20260911_0026
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260911_0027"
down_revision: str | None = "20260911_0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "user_game_profiles",
        sa.Column("skin_code", sa.String(length=32), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_game_profiles", "skin_code")
