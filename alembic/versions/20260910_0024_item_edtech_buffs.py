"""Replace game_items' RPG stat bonuses with EdTech reward buffs.

An equipped item used to move the same four combat numbers a class moves
(`bonus_max_hp`, `bonus_damage_permille`, `bonus_starting_mana`,
`bonus_defence`) -- the "cày đồ" pay-to-win loop the EdTech redesign removes.
`combat_stats.resolve` no longer takes equipment as an input at all, so those
columns have nothing left to feed. What replaces them is a percentage on a
match or lesson's EXP/Gold payout (`bonus_exp_permille`, `bonus_gold_permille`),
applied in `settlement.GameSettlementService._reward_bonus` -- gear can grow a
reward already won, never the odds of winning it.

Revision ID: 20260910_0024
Revises: 20260910_0023
Create Date: 2026-09-10
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260910_0024"
down_revision: str | None = "20260910_0023"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "game_items",
        sa.Column("bonus_exp_permille", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "game_items",
        sa.Column("bonus_gold_permille", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_column("game_items", "bonus_max_hp")
    op.drop_column("game_items", "bonus_damage_permille")
    op.drop_column("game_items", "bonus_starting_mana")
    op.drop_column("game_items", "bonus_defence")


def downgrade() -> None:
    op.add_column(
        "game_items",
        sa.Column("bonus_max_hp", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "game_items",
        sa.Column("bonus_damage_permille", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "game_items",
        sa.Column("bonus_starting_mana", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "game_items",
        sa.Column("bonus_defence", sa.Integer(), nullable=False, server_default="0"),
    )
    op.drop_column("game_items", "bonus_exp_permille")
    op.drop_column("game_items", "bonus_gold_permille")
