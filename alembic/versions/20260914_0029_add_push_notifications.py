"""Add push notifications: device tokens and a record of what was sent.

`user_device_tokens` holds one row per app install reachable through Firebase
Cloud Messaging. The token is unique across users, so a phone that changes
hands between accounts notifies only the one signed in.

`notification_dispatches` keeps a scheduled push to once per occasion. The
sender inserts a row per recipient before sending and sends only to the rows it
actually inserted, so a restart -- or a second process running the same sweep --
cannot deliver the same reminder twice.

Revision ID: 20260914_0029
Revises: 20260913_0028
Create Date: 2026-09-14
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260914_0029"
down_revision: str | None = "20260913_0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

device_type = postgresql.ENUM("ANDROID", "IOS", name="device_type", create_type=False)


def upgrade() -> None:
    device_type.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "user_device_tokens",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("fcm_token", sa.Text(), nullable=False),
        sa.Column("device_type", device_type, nullable=False, server_default="ANDROID"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("fcm_token", name="uq_user_device_tokens_fcm_token"),
    )
    op.create_index("ix_user_device_tokens_user_id", "user_device_tokens", ["user_id"])

    op.create_table(
        "notification_dispatches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("dedupe_key", sa.String(length=64), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id",
            "kind",
            "dedupe_key",
            name="uq_notification_dispatches_user_id_kind_dedupe_key",
        ),
    )


def downgrade() -> None:
    op.drop_table("notification_dispatches")
    op.drop_index("ix_user_device_tokens_user_id", table_name="user_device_tokens")
    op.drop_table("user_device_tokens")
    device_type.drop(op.get_bind(), checkfirst=True)
