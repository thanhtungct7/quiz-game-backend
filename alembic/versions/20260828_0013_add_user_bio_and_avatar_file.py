"""Add user bio and the Drive file id backing an uploaded avatar.

Revision ID: 20260828_0013
Revises: 20260826_0012
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260828_0013"
down_revision: str | None = "20260826_0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("bio", sa.String(length=300), nullable=True))
    op.add_column("users", sa.Column("avatar_file_id", sa.String(length=64), nullable=True))


def downgrade() -> None:
    op.drop_column("users", "avatar_file_id")
    op.drop_column("users", "bio")
