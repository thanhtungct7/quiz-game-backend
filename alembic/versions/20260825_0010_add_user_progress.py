"""Add user_challenge_progress and user_lesson_progress tables to track
per-user learning progress.

Revision ID: 20260825_0010
Revises: 20260823_0009
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260825_0010"
down_revision: str | None = "20260823_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

lesson_progress_status = sa.Enum(
    "NOT_STARTED", "IN_PROGRESS", "COMPLETED", name="lesson_progress_status"
)


def upgrade() -> None:
    op.create_table(
        "user_challenge_progress",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("challenge_id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("last_selected_option_id", sa.String(length=36), nullable=True),
        sa.Column("mastered", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("attempts_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("mastered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "last_attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["last_selected_option_id"], ["challenge_options.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "challenge_id", name="uq_user_challenge_progress_user_id_challenge_id"
        ),
    )
    op.create_index(
        op.f("ix_user_challenge_progress_user_id"),
        "user_challenge_progress",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_challenge_progress_challenge_id"),
        "user_challenge_progress",
        ["challenge_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_user_challenge_progress_lesson_id"),
        "user_challenge_progress",
        ["lesson_id"],
        unique=False,
    )

    op.create_table(
        "user_lesson_progress",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column(
            "status", lesson_progress_status, nullable=False, server_default="NOT_STARTED"
        ),
        sa.Column("correct_challenge_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("total_challenge_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "user_id", "lesson_id", name="uq_user_lesson_progress_user_id_lesson_id"
        ),
    )
    op.create_index(
        op.f("ix_user_lesson_progress_user_id"), "user_lesson_progress", ["user_id"], unique=False
    )
    op.create_index(
        op.f("ix_user_lesson_progress_lesson_id"),
        "user_lesson_progress",
        ["lesson_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_user_lesson_progress_lesson_id"), table_name="user_lesson_progress")
    op.drop_index(op.f("ix_user_lesson_progress_user_id"), table_name="user_lesson_progress")
    op.drop_table("user_lesson_progress")
    lesson_progress_status.drop(op.get_bind(), checkfirst=True)

    op.drop_index(
        op.f("ix_user_challenge_progress_lesson_id"), table_name="user_challenge_progress"
    )
    op.drop_index(
        op.f("ix_user_challenge_progress_challenge_id"), table_name="user_challenge_progress"
    )
    op.drop_index(
        op.f("ix_user_challenge_progress_user_id"), table_name="user_challenge_progress"
    )
    op.drop_table("user_challenge_progress")
