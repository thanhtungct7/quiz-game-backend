"""Add TIME_BONUS to skill_effect.

The Knowledge Lifeline catalog (`services/game/catalog.py`) now covers three
effects -- REMOVE_OPTIONS, COMBO_KEEP and TIME_BONUS, the last one new: it
adds `magnitude` seconds to the time a player's next answer is scored
against. Enum values cannot be added by autogenerate, and PostgreSQL has no
way to remove one, so the downgrade is a no-op.

Revision ID: 20260910_0023
Revises: 20260906_0022
Create Date: 2026-09-10
"""

from alembic import op

revision: str = "20260910_0023"
down_revision: str | None = "20260906_0022"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.execute("ALTER TYPE skill_effect ADD VALUE IF NOT EXISTS 'TIME_BONUS'")


def downgrade() -> None:
    """PostgreSQL cannot drop an enum value; nothing to undo."""
