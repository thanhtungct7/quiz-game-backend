"""Add health, damage and combo tracking to duo matches, plus the KNOCKOUT end reason.

Revision ID: 20260829_0015
Revises: 20260829_0014
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260829_0015"
down_revision: str | None = "20260829_0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

STARTING_HP = "100"

_ROUND_COLUMNS = (
    "player_one_damage",
    "player_two_damage",
    "player_one_hp_after",
    "player_two_hp_after",
    "player_one_combo",
    "player_two_combo",
)


def upgrade() -> None:
    # Adding a value to an existing PostgreSQL enum cannot be expressed with
    # sa.Enum and is never produced by --autogenerate, so it is written by hand.
    # It goes first, and nothing in this migration stores the new value: on
    # PostgreSQL a value added inside a transaction may not be used by that
    # same transaction.
    op.execute("ALTER TYPE duo_match_end_reason ADD VALUE IF NOT EXISTS 'KNOCKOUT'")

    op.add_column(
        "duo_matches",
        sa.Column(
            "player_one_hp_left", sa.Integer(), nullable=False, server_default=STARTING_HP
        ),
    )
    op.add_column(
        "duo_matches",
        sa.Column(
            "player_two_hp_left", sa.Integer(), nullable=False, server_default=STARTING_HP
        ),
    )
    for column in _ROUND_COLUMNS:
        op.add_column(
            "duo_match_rounds",
            sa.Column(column, sa.Integer(), nullable=False, server_default="0"),
        )


def downgrade() -> None:
    for column in reversed(_ROUND_COLUMNS):
        op.drop_column("duo_match_rounds", column)
    op.drop_column("duo_matches", "player_two_hp_left")
    op.drop_column("duo_matches", "player_one_hp_left")
    # PostgreSQL cannot remove a value from an enum type, so KNOCKOUT stays.
    # Nothing references it once the columns above are gone.
