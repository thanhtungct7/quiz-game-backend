"""Add benchmark_exam_attempts: the Benchmark Exam, drawn and graded server-side.

Until now the client drew the paper, counted its own correct answers and posted
only the verdict, so any caller could lift any cap it had reached. A sitting is
now a row: the paper it was served, what was answered, and the grade the server
arrived at. Clearing a cap happens only as the outcome of grading one.

The partial unique index keeps a player to one IN_PROGRESS sitting at a time.

Revision ID: 20260913_0028
Revises: 20260911_0027
Create Date: 2026-09-13
"""

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260913_0028"
down_revision: str | None = "20260911_0027"
branch_labels: str | None = None
depends_on: str | None = None

benchmark_attempt_status = postgresql.ENUM(
    "IN_PROGRESS",
    "PASSED",
    "FAILED",
    "ABANDONED",
    name="benchmark_attempt_status",
    create_type=False,
)


def upgrade() -> None:
    benchmark_attempt_status.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "benchmark_exam_attempts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("cap_level", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            benchmark_attempt_status,
            nullable=False,
            server_default="IN_PROGRESS",
        ),
        sa.Column("question_ids", sa.ARRAY(sa.String(length=36)), nullable=False),
        sa.Column(
            "answers",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("total_count", sa.Integer(), nullable=False),
        sa.Column("correct_count", sa.Integer(), nullable=True),
        sa.Column("pass_percent", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_benchmark_exam_attempts_user_id", "benchmark_exam_attempts", ["user_id"]
    )
    op.create_index(
        "uq_benchmark_exam_attempts_one_in_progress",
        "benchmark_exam_attempts",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status = 'IN_PROGRESS'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_benchmark_exam_attempts_one_in_progress", table_name="benchmark_exam_attempts"
    )
    op.drop_index("ix_benchmark_exam_attempts_user_id", table_name="benchmark_exam_attempts")
    op.drop_table("benchmark_exam_attempts")
    benchmark_attempt_status.drop(op.get_bind(), checkfirst=True)
