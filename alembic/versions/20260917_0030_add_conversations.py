"""Add AI conversation practice: sessions and their messages.

`conversation_sessions` is one practice conversation on one scenario, with the
learner's CEFR band fixed at the start and the end-of-session feedback stored
once it has been generated. `conversation_messages` is its transcript in `seq`
order; an AI line caches its Vietnamese translation the first time it is asked
for.

Revision ID: 20260917_0030
Revises: 20260914_0029
Create Date: 2026-09-17
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260917_0030"
down_revision: str | None = "20260914_0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

conversation_status = postgresql.ENUM(
    "ACTIVE", "FINISHED", name="conversation_status", create_type=False
)
conversation_message_role = postgresql.ENUM(
    "USER", "ASSISTANT", name="conversation_message_role", create_type=False
)


def upgrade() -> None:
    conversation_status.create(op.get_bind(), checkfirst=True)
    conversation_message_role.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "conversation_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("scenario_code", sa.String(length=50), nullable=False),
        sa.Column("cefr", sa.String(length=2), nullable=False),
        sa.Column("status", conversation_status, nullable=False, server_default="ACTIVE"),
        sa.Column("user_turns", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("goal_reached", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("feedback", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("score", sa.Integer(), nullable=True),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_conversation_sessions_user_id_started_at",
        "conversation_sessions",
        ["user_id", "started_at"],
    )
    op.create_table(
        "conversation_messages",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("role", conversation_message_role, nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("translation_vi", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["session_id"], ["conversation_sessions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "seq", name="uq_conversation_messages_session_id_seq"),
    )


def downgrade() -> None:
    op.drop_table("conversation_messages")
    op.drop_index("ix_conversation_sessions_user_id_started_at", table_name="conversation_sessions")
    op.drop_table("conversation_sessions")
    conversation_message_role.drop(op.get_bind(), checkfirst=True)
    conversation_status.drop(op.get_bind(), checkfirst=True)
