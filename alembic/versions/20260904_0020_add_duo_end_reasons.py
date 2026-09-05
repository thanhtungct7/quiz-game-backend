"""Add DECK_CLEARED and TIME_UP to duo_match_end_reason.

The realtime duo engine ends a match in three ways -- a knockout, a player
clearing their deck, or the clock running out -- and only the first of those
already existed. Enum values cannot be added by autogenerate, and PostgreSQL
has no way to remove one, so the downgrade is a no-op: the two values are left
in place and simply stop being written.

Revision ID: 20260904_0020
Revises: 20260904_0019
Create Date: 2026-09-04
"""

from alembic import op

revision: str = "20260904_0020"
down_revision: str | None = "20260904_0019"
branch_labels: str | None = None
depends_on: str | None = None

NEW_VALUES = ("DECK_CLEARED", "TIME_UP")


def upgrade() -> None:
    for value in NEW_VALUES:
        op.execute(f"ALTER TYPE duo_match_end_reason ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    """PostgreSQL cannot drop an enum value; nothing to undo."""
