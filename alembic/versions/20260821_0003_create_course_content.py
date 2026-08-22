"""Create course content tables (courses, units, lessons, challenges, challenge_options).

Revision ID: 20260821_0003
Revises: d0e8f930867b
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260821_0003"
down_revision: str | None = "d0e8f930867b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "courses",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("image_src", sa.String(length=255), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "units",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("course_id", sa.String(length=36), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["course_id"], ["courses.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_units_course_id"), "units", ["course_id"], unique=False)

    op.create_table(
        "lessons",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=100), nullable=False),
        sa.Column("unit_id", sa.String(length=36), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_lessons_unit_id"), "lessons", ["unit_id"], unique=False)

    challenge_type = sa.Enum("SELECT", "ASSIST", name="challenge_type")

    op.create_table(
        "challenges",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("lesson_id", sa.String(length=36), nullable=False),
        sa.Column("type", challenge_type, nullable=False),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("order_index", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["lesson_id"], ["lessons.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_challenges_lesson_id"), "challenges", ["lesson_id"], unique=False)

    op.create_table(
        "challenge_options",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("challenge_id", sa.String(length=36), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("correct", sa.Boolean(), nullable=False),
        sa.Column("image_src", sa.String(length=255), nullable=True),
        sa.Column("audio_src", sa.String(length=255), nullable=True),
        sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_challenge_options_challenge_id"),
        "challenge_options",
        ["challenge_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_challenge_options_challenge_id"), table_name="challenge_options")
    op.drop_table("challenge_options")
    op.drop_index(op.f("ix_challenges_lesson_id"), table_name="challenges")
    op.drop_table("challenges")
    sa.Enum(name="challenge_type").drop(op.get_bind(), checkfirst=True)
    op.drop_index(op.f("ix_lessons_unit_id"), table_name="lessons")
    op.drop_table("lessons")
    op.drop_index(op.f("ix_units_course_id"), table_name="units")
    op.drop_table("units")
    op.drop_table("courses")
