"""Add energy, daily streaks, loot items and ladder seasons.

Revision ID: 20260829_0017
Revises: 20260829_0016
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260829_0017"
down_revision: str | None = "20260829_0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

MAX_ENERGY = "5"
DEFAULT_SEASON_RATING = "1000"

item_kind = sa.Enum("EQUIPMENT", "SKIN", "CARD", name="item_kind")
equipment_slot = sa.Enum("WEAPON", "ARMOR", "TRINKET", name="equipment_slot")
item_rarity = sa.Enum("COMMON", "RARE", "EPIC", "LEGENDARY", name="item_rarity")


def upgrade() -> None:
    op.add_column(
        "user_game_profiles",
        sa.Column("energy", sa.Integer(), nullable=False, server_default=MAX_ENERGY),
    )
    op.add_column(
        "user_game_profiles",
        sa.Column(
            "energy_updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.add_column(
        "user_game_profiles",
        sa.Column("day_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column(
        "user_game_profiles",
        sa.Column("best_day_streak", sa.Integer(), nullable=False, server_default="0"),
    )
    op.add_column("user_game_profiles", sa.Column("last_active_date", sa.Date(), nullable=True))

    op.create_table(
        "user_daily_activity",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("activity_date", sa.Date(), nullable=False),
        sa.Column("lessons_completed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("matches_played", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "activity_date", name="uq_user_daily_activity_user_id_activity_date"
        ),
    )
    op.create_index("ix_user_daily_activity_user_id", "user_daily_activity", ["user_id"])

    op.create_table(
        "game_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("kind", item_kind, nullable=False),
        sa.Column("slot", equipment_slot, nullable=True),
        sa.Column("rarity", item_rarity, nullable=False),
        sa.Column("bonus_max_hp", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus_damage_permille", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus_starting_mana", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("image_src", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_game_items_code", "game_items", ["code"], unique=True)

    op.create_table(
        "user_items",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["game_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "item_id", name="uq_user_items_user_id_item_id"),
    )
    op.create_index("ix_user_items_user_id", "user_items", ["user_id"])
    op.create_index("ix_user_items_item_id", "user_items", ["item_id"])

    op.create_table(
        "user_equipment",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("slot", equipment_slot, nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["game_items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "slot", name="uq_user_equipment_user_id_slot"),
    )
    op.create_index("ix_user_equipment_user_id", "user_equipment", ["user_id"])
    op.create_index("ix_user_equipment_item_id", "user_equipment", ["item_id"])

    op.create_table(
        "loot_grants",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("ref_id", sa.String(length=36), nullable=False),
        sa.Column("item_id", sa.String(length=36), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["game_items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        # The idempotency key: one chest per player per match, ever.
        sa.UniqueConstraint("user_id", "ref_id", name="uq_loot_grants_user_id_ref_id"),
    )
    op.create_index("ix_loot_grants_user_id", "loot_grants", ["user_id"])

    op.create_table(
        "game_seasons",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("starts_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ends_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_game_seasons_code", "game_seasons", ["code"], unique=True)
    op.create_index("ix_game_seasons_is_active", "game_seasons", ["is_active"])

    op.create_table(
        "season_ratings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("season_id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column(
            "rating", sa.Integer(), nullable=False, server_default=DEFAULT_SEASON_RATING
        ),
        sa.Column(
            "peak_rating", sa.Integer(), nullable=False, server_default=DEFAULT_SEASON_RATING
        ),
        sa.Column("matches_played", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draws", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["season_id"], ["game_seasons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("season_id", "user_id", name="uq_season_ratings_season_id_user_id"),
    )
    op.create_index("ix_season_ratings_season_id", "season_ratings", ["season_id"])
    op.create_index("ix_season_ratings_user_id", "season_ratings", ["user_id"])
    # The ladder orders by this.
    op.create_index("ix_season_ratings_rating", "season_ratings", ["rating"])


def downgrade() -> None:
    op.drop_table("season_ratings")
    op.drop_table("game_seasons")
    op.drop_table("loot_grants")
    op.drop_table("user_equipment")
    op.drop_table("user_items")
    op.drop_table("game_items")
    op.drop_table("user_daily_activity")
    op.drop_column("user_game_profiles", "last_active_date")
    op.drop_column("user_game_profiles", "best_day_streak")
    op.drop_column("user_game_profiles", "day_streak")
    op.drop_column("user_game_profiles", "energy_updated_at")
    op.drop_column("user_game_profiles", "energy")
    item_rarity.drop(op.get_bind(), checkfirst=True)
    equipment_slot.drop(op.get_bind(), checkfirst=True)
    item_kind.drop(op.get_bind(), checkfirst=True)
