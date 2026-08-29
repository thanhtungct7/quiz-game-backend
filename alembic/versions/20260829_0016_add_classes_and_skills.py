"""Add character classes, the skill tree, loadouts and the in-match skill log.

Revision ID: 20260829_0016
Revises: 20260829_0015
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260829_0016"
down_revision: str | None = "20260829_0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

skill_effect = sa.Enum(
    "DOUBLE_DAMAGE",
    "DAMAGE_REDUCTION",
    "HEAL",
    "TIME_PENALTY",
    "REMOVE_OPTIONS",
    "MANA_BURN",
    "COMBO_KEEP",
    "EXECUTE",
    name="skill_effect",
)
skill_unlock_kind = sa.Enum(
    "STARTER", "LEVEL_GOLD", "UNIT_COMPLETION", name="skill_unlock_kind"
)


def upgrade() -> None:
    op.create_table(
        "game_classes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("max_hp", sa.Integer(), nullable=False),
        sa.Column("damage_permille", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("starting_mana", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.PrimaryKeyConstraint("id"),
    )
    # Unique because skills and profiles reference a class by code, not by id.
    op.create_index("ix_game_classes_code", "game_classes", ["code"], unique=True)

    op.create_table(
        "skills",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("code", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("effect", skill_effect, nullable=False),
        sa.Column("class_code", sa.String(length=32), nullable=True),
        sa.Column("tier", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("parent_code", sa.String(length=32), nullable=True),
        sa.Column("mana_cost", sa.Integer(), nullable=False),
        sa.Column("magnitude", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("duration_rounds", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unlock_kind", skill_unlock_kind, nullable=False),
        sa.Column("unlock_level", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("gold_price", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unlock_unit_id", sa.String(length=36), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("sort_order", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["class_code"], ["game_classes.code"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["unlock_unit_id"], ["units.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skills_code", "skills", ["code"], unique=True)
    op.create_index("ix_skills_class_code", "skills", ["class_code"])
    op.create_index("ix_skills_unlock_unit_id", "skills", ["unlock_unit_id"])

    op.create_table(
        "user_skills",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.Column(
            "acquired_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "skill_id", name="uq_user_skills_user_id_skill_id"),
    )
    op.create_index("ix_user_skills_user_id", "user_skills", ["user_id"])
    op.create_index("ix_user_skills_skill_id", "user_skills", ["skill_id"])

    op.create_table(
        "user_skill_loadouts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("slot_index", sa.Integer(), nullable=False),
        sa.Column("skill_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "slot_index", name="uq_user_skill_loadouts_user_id_slot_index"
        ),
    )
    op.create_index("ix_user_skill_loadouts_user_id", "user_skill_loadouts", ["user_id"])
    op.create_index("ix_user_skill_loadouts_skill_id", "user_skill_loadouts", ["skill_id"])

    op.create_table(
        "duo_match_skill_uses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("match_id", sa.String(length=36), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("skill_id", sa.String(length=36), nullable=True),
        sa.Column("skill_code", sa.String(length=32), nullable=False),
        sa.Column("mana_spent", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["match_id"], ["duo_matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["skill_id"], ["skills.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_duo_match_skill_uses_match_id", "duo_match_skill_uses", ["match_id"]
    )
    op.create_index("ix_duo_match_skill_uses_user_id", "duo_match_skill_uses", ["user_id"])
    op.create_index(
        "ix_duo_match_skill_uses_skill_id", "duo_match_skill_uses", ["skill_id"]
    )

    op.add_column(
        "user_game_profiles", sa.Column("class_code", sa.String(length=32), nullable=True)
    )
    op.add_column(
        "user_game_profiles",
        sa.Column("class_chosen_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_user_game_profiles_class_code_game_classes",
        "user_game_profiles",
        "game_classes",
        ["class_code"],
        ["code"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_user_game_profiles_class_code_game_classes",
        "user_game_profiles",
        type_="foreignkey",
    )
    op.drop_column("user_game_profiles", "class_chosen_at")
    op.drop_column("user_game_profiles", "class_code")

    op.drop_table("duo_match_skill_uses")
    op.drop_table("user_skill_loadouts")
    op.drop_table("user_skills")
    op.drop_table("skills")
    op.drop_table("game_classes")
    skill_unlock_kind.drop(op.get_bind(), checkfirst=True)
    skill_effect.drop(op.get_bind(), checkfirst=True)
