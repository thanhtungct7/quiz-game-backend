"""Add challenge explanation, challenge_options.order_index, and unique
constraints on order_index per parent (units, lessons, challenges,
challenge_options).

Revision ID: 20260822_0005
Revises: 20260822_0004
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260822_0005"
down_revision: str | None = "20260822_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("challenges", sa.Column("explanation", sa.Text(), nullable=True))

    op.add_column(
        "challenge_options", sa.Column("order_index", sa.Integer(), nullable=True)
    )
    # Backfill existing rows so the column can become NOT NULL: number each
    # option 1..N within its challenge, ordered by id (its prior de-facto order).
    op.execute(
        """
        UPDATE challenge_options AS co
        SET order_index = ranked.row_number
        FROM (
            SELECT id, ROW_NUMBER() OVER (PARTITION BY challenge_id ORDER BY id) AS row_number
            FROM challenge_options
        ) AS ranked
        WHERE co.id = ranked.id
        """
    )
    op.alter_column("challenge_options", "order_index", nullable=False)

    op.create_unique_constraint(
        "uq_units_course_id_order_index", "units", ["course_id", "order_index"]
    )
    op.create_unique_constraint(
        "uq_lessons_unit_id_order_index", "lessons", ["unit_id", "order_index"]
    )
    op.create_unique_constraint(
        "uq_challenges_lesson_id_order_index", "challenges", ["lesson_id", "order_index"]
    )
    op.create_unique_constraint(
        "uq_challenge_options_challenge_id_order_index",
        "challenge_options",
        ["challenge_id", "order_index"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_challenge_options_challenge_id_order_index",
        "challenge_options",
        type_="unique",
    )
    op.drop_constraint("uq_challenges_lesson_id_order_index", "challenges", type_="unique")
    op.drop_constraint("uq_lessons_unit_id_order_index", "lessons", type_="unique")
    op.drop_constraint("uq_units_course_id_order_index", "units", type_="unique")

    op.drop_column("challenge_options", "order_index")
    op.drop_column("challenges", "explanation")
