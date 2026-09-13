"""Add gold_price to game_items, so a cosmetic can be bought rather than only dropped.

Same convention as `skills.gold_price`: 0 means the row is not for sale, which
keeps every equipment item chest-only exactly as it is today. Only SKIN and
CARD rows are priced, and both carry `bonus_exp_permille = bonus_gold_permille
= 0`, so the shop can never sell an advantage -- see
`services/game/catalog.ITEMS`.

Revision ID: 20260911_0026
Revises: 20260910_0025
Create Date: 2026-09-11
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260911_0026"
down_revision: str | None = "20260910_0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "game_items",
        sa.Column("gold_price", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("game_items", "gold_price")
