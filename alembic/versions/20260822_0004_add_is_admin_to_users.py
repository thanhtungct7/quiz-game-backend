"""Add is_admin flag to users.

Revision ID: 20260822_0004
Revises: 20260821_0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260822_0004"
down_revision: str | None = "20260821_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
