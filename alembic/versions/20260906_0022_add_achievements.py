"""Add the achievement catalog and who has unlocked what.

Two tables, one of them a catalog upserted on `code` at every startup like the
class, item and monster catalogs, and one recording that a player crossed a
threshold. There is no event log: an achievement is a `(metric, threshold)`
pair measured against counters that already exist, so nothing had to start
being recorded for this to work and existing accounts unlock retroactively.

The unique (user_id, achievement_id) is the whole concurrency story -- a sync
offers everything currently earned and lets the constraint drop what was
already held, which is what makes running it twice harmless.

Revision ID: 20260906_0022
Revises: 20260906_0021
Create Date: 2026-09-06
"""

import sqlalchemy as sa

from alembic import op

revision: str = "20260906_0022"
down_revision: str | None = "20260906_0021"
branch_labels: str | None = None
depends_on: str | None = None

achievement_category = sa.Enum(
    "PROGRESSION",
    "LEARNING",
    "PVP",
    "PVE",
    name="achievement_category",
)
achievement_metric = sa.Enum(
    "LEVEL",
    "DAY_STREAK",
    "BEST_DAY_STREAK",
    "CHALLENGES_MASTERED",
    "TOTAL_ATTEMPTS",
    "LESSONS_COMPLETED",
    "PVP_WINS",
    "PVP_RATING",
    "PVP_BEST_STREAK",
    "BATTLES_WON",
    name="achievement_metric",
)


def upgrade() -> None:
    op.create_table(
        "achievements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False, server_default=""),
        sa.Column("category", achievement_category, nullable=False),
        sa.Column("metric", achievement_metric, nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("icon_code", sa.String(length=32), nullable=False, server_default=""),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_hidden", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_achievements_code", "achievements", ["code"], unique=True)

    op.create_table(
        "user_achievements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("achievement_id", sa.String(length=36), nullable=False),
        sa.Column(
            "unlocked_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["achievement_id"], ["achievements.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),
    )
    op.create_index("ix_user_achievements_user_id", "user_achievements", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_user_achievements_user_id", table_name="user_achievements")
    op.drop_table("user_achievements")
    op.drop_index("ix_achievements_code", table_name="achievements")
    op.drop_table("achievements")
    achievement_metric.drop(op.get_bind(), checkfirst=True)
    achievement_category.drop(op.get_bind(), checkfirst=True)
