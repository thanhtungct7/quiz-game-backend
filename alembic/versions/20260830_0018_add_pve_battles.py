"""Add the monster catalog and PvE lesson battles.

Revision ID: 20260830_0018
Revises: 20260829_0017
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260830_0018"
down_revision: str | None = "20260829_0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PERMILLE_ONE = "1000"

battle_status = sa.Enum("IN_PROGRESS", "WON", "LOST", "ABANDONED", name="battle_status")
battle_end_reason = sa.Enum(
    "MONSTER_DOWN",
    "PLAYER_DOWN",
    "OUT_OF_QUESTIONS",
    "LEFT",
    "CANCELLED",
    name="battle_end_reason",
)

# PvE pays out through the same ledger duo does, so the reason enum grows two
# values. PostgreSQL allows this inside a transaction as long as the new values
# are not used in that same transaction -- the seeder runs long after.
NEW_GOLD_REASONS = ("BATTLE_WIN", "BATTLE_REPLAY")


def upgrade() -> None:
    for value in NEW_GOLD_REASONS:
        op.execute(f"ALTER TYPE gold_reason ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "monsters",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("tier", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("max_hp", sa.Integer(), nullable=False),
        sa.Column("attack_damage", sa.Integer(), nullable=False),
        sa.Column("damage_reduction_permille", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("enrage_after_rounds", sa.Integer(), nullable=False, server_default="5"),
        sa.Column(
            "enrage_multiplier_permille",
            sa.Integer(),
            nullable=False,
            server_default=PERMILLE_ONE,
        ),
        sa.Column("is_boss", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("weak_topic_id", sa.String(length=36), nullable=True),
        sa.Column("art_code", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.ForeignKeyConstraint(["weak_topic_id"], ["topics.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_monsters_code", "monsters", ["code"], unique=True)

    op.create_table(
        "lesson_battles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("monster_code", sa.String(length=32), nullable=False),
        sa.Column("status", battle_status, nullable=False, server_default="IN_PROGRESS"),
        sa.Column("end_reason", battle_end_reason, nullable=True),
        sa.Column("player_hp_left", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("monster_hp_left", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rounds_played", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("correct_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("best_combo", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("exp_awarded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("gold_awarded", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("first_clear", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_lesson_battles_user_id", "lesson_battles", ["user_id"])
    op.create_index("ix_lesson_battles_lesson_id", "lesson_battles", ["lesson_id"])
    # History is read newest-first for one player, and the preview endpoint
    # asks "has this player already cleared this lesson".
    op.create_index(
        "ix_lesson_battles_user_id_lesson_id", "lesson_battles", ["user_id", "lesson_id"]
    )


def downgrade() -> None:
    op.drop_table("lesson_battles")
    op.drop_index("ix_monsters_code", table_name="monsters")
    op.drop_table("monsters")
    battle_end_reason.drop(op.get_bind(), checkfirst=True)
    battle_status.drop(op.get_bind(), checkfirst=True)
    # gold_reason keeps its two new values: PostgreSQL cannot drop an enum
    # label, and ledger rows may already reference them.
