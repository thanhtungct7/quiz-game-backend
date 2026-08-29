"""Add user_game_profiles and gold_transactions for the game economy.

Revision ID: 20260829_0014
Revises: 20260828_0013
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260829_0014"
down_revision: str | None = "20260828_0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

gold_reason = sa.Enum(
    "MATCH_WIN",
    "MATCH_LOSS",
    "MATCH_DRAW",
    "SKILL_UNLOCK",
    "ITEM_PURCHASE",
    "CLASS_CHANGE",
    "LOOT_CHEST",
    name="gold_reason",
)


def upgrade() -> None:
    op.create_table(
        "user_game_profiles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("total_exp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gold", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_user_game_profiles_user_id", "user_game_profiles", ["user_id"], unique=True
    )

    op.create_table(
        "gold_transactions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("amount", sa.Integer(), nullable=False),
        sa.Column("reason", gold_reason, nullable=False),
        sa.Column("ref_id", sa.String(length=36), nullable=False),
        sa.Column("balance_after", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        # The idempotency key: a second settle of the same match cannot insert a
        # second row, so it cannot pay out twice.
        sa.UniqueConstraint(
            "user_id", "reason", "ref_id", name="uq_gold_transactions_user_id_reason_ref_id"
        ),
    )
    op.create_index("ix_gold_transactions_user_id", "gold_transactions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_gold_transactions_user_id", table_name="gold_transactions")
    op.drop_table("gold_transactions")
    op.drop_index("ix_user_game_profiles_user_id", table_name="user_game_profiles")
    op.drop_table("user_game_profiles")
    gold_reason.drop(op.get_bind(), checkfirst=True)
