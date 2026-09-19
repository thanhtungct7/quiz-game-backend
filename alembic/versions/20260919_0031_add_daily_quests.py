"""Add daily quests: the quest catalog, each player's quests per day, and the
activity chests those quests open.

`daily_quest_templates` is seeded on startup from `services/game/quest_catalog.py`
the way the other catalogs are. `user_daily_quests` holds the four quests a
player drew for one day, with the goal and rewards copied off the template.
`user_daily_activity_chests` records each chest opened, one row per chest, so
its unique constraint is the double-claim guard.

Quest rewards go through the gold ledger like every other payout, so the
`gold_reason` enum grows two values.

Revision ID: 20260919_0031
Revises: 20260917_0030
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260919_0031"
down_revision: str | None = "20260917_0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Allowed inside the migration's transaction as long as nothing in that same
# transaction uses them -- the first quest reward is paid long after.
NEW_GOLD_REASONS = ("DAILY_QUEST", "ACTIVITY_CHEST")


def upgrade() -> None:
    for value in NEW_GOLD_REASONS:
        op.execute(f"ALTER TYPE gold_reason ADD VALUE IF NOT EXISTS '{value}'")

    op.create_table(
        "daily_quest_templates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("quest_type", sa.String(length=32), nullable=False),
        sa.Column("difficulty", sa.String(length=16), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column("activity_points", sa.Integer(), nullable=False),
        sa.Column("reward_gold", sa.Integer(), nullable=False),
        sa.Column("reward_exp", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_daily_quest_templates_code", "daily_quest_templates", ["code"], unique=True)

    op.create_table(
        "user_daily_quests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("template_id", sa.String(length=36), nullable=False),
        sa.Column("quest_date", sa.Date(), nullable=False),
        sa.Column("quest_type", sa.String(length=32), nullable=False),
        sa.Column("difficulty", sa.String(length=16), nullable=False),
        sa.Column("target", sa.Integer(), nullable=False),
        sa.Column("activity_points", sa.Integer(), nullable=False),
        sa.Column("reward_gold", sa.Integer(), nullable=False),
        sa.Column("reward_exp", sa.Integer(), nullable=False),
        sa.Column("slot", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("progress", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["template_id"], ["daily_quest_templates.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "template_id",
            "quest_date",
            name="uq_user_daily_quests_user_id_template_id_quest_date",
        ),
    )
    op.create_index(
        "ix_user_daily_quests_user_id_quest_date",
        "user_daily_quests",
        ["user_id", "quest_date"],
    )

    op.create_table(
        "user_daily_activity_chests",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("quest_date", sa.Date(), nullable=False),
        sa.Column("milestone", sa.Integer(), nullable=False),
        sa.Column(
            "claimed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "quest_date",
            "milestone",
            name="uq_user_daily_activity_chests_user_id_quest_date_milestone",
        ),
    )
    op.create_index(
        "ix_user_daily_activity_chests_user_id",
        "user_daily_activity_chests",
        ["user_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_user_daily_activity_chests_user_id", table_name="user_daily_activity_chests")
    op.drop_table("user_daily_activity_chests")
    op.drop_index("ix_user_daily_quests_user_id_quest_date", table_name="user_daily_quests")
    op.drop_table("user_daily_quests")
    op.drop_index("ix_daily_quest_templates_code", table_name="daily_quest_templates")
    op.drop_table("daily_quest_templates")
    # gold_reason keeps its two new values: PostgreSQL cannot drop an enum
    # label, and ledger rows may already reference them.
