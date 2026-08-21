"""Add user profile fields and refresh tokens.

Revision ID: 20260820_0002
Revises: 20260819_0001
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260820_0002"
down_revision: str | None = "20260819_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("users", sa.Column("username", sa.String(length=50), nullable=True))
    op.add_column("users", sa.Column("avatar_url", sa.String(length=255), nullable=True))
    op.add_column("users", sa.Column("google_subject", sa.String(length=255), nullable=True))
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.Text(),
        nullable=True,
    )
    op.create_index(
        op.f("ix_users_google_subject"),
        "users",
        ["google_subject"],
        unique=True,
    )

    op.create_table(
        "refresh_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("token_hash", sa.String(length=255), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("device_name", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_refresh_tokens_token_hash"),
        "refresh_tokens",
        ["token_hash"],
        unique=True,
    )
    op.create_index(
        op.f("ix_refresh_tokens_user_id"),
        "refresh_tokens",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_refresh_tokens_user_id"), table_name="refresh_tokens")
    op.drop_index(op.f("ix_refresh_tokens_token_hash"), table_name="refresh_tokens")
    op.drop_table("refresh_tokens")
    op.drop_index(op.f("ix_users_google_subject"), table_name="users")
    op.drop_column("users", "google_subject")
    op.alter_column(
        "users",
        "hashed_password",
        existing_type=sa.Text(),
        nullable=False,
    )
    op.drop_column("users", "avatar_url")
    op.drop_column("users", "username")
