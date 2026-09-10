"""Add the DEF stat to classes and equipment.

Defence is flat damage taken off every incoming blow, not a percentage, so it
is an ordinary integer rather than a permille figure like `damage_permille`.
It feeds the `defender_flat_reduction` parameter `combat.resolve_blow` and
`pve.monster.monster_attack` already accept, so nothing about how a blow is
resolved changes -- only how many numbers reach it.

Columns only. The values live in `services/game/catalog.py` and are written by
the seeder, which upserts on `code` at every startup; a fresh column defaulting
to zero is simply "no defence yet" until that runs.

Revision ID: 20260906_0021
Revises: 20260904_0020
Create Date: 2026-09-06
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20260906_0021"
down_revision: str | None = "20260904_0020"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "game_classes",
        sa.Column("defence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "game_items",
        sa.Column("bonus_defence", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("game_items", "bonus_defence")
    op.drop_column("game_classes", "defence")
