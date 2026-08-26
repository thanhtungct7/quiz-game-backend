"""Add duo_matches, duo_match_rounds and duo_ratings tables for 1v1 PvP.

Revision ID: 20260826_0011
Revises: 20260825_0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260826_0011"
down_revision: str | None = "20260825_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

duo_match_mode = sa.Enum("RANDOM", "FRIEND", name="duo_match_mode")
duo_match_status = sa.Enum(
    "WAITING", "IN_PROGRESS", "FINISHED", "ABANDONED", "CANCELLED", name="duo_match_status"
)
duo_match_end_reason = sa.Enum(
    "COMPLETED", "OPPONENT_LEFT", "OPPONENT_TIMEOUT", "CANCELLED", name="duo_match_end_reason"
)
# Already created by 20260822_0006; reference it without re-creating the type.
challenge_difficulty = postgresql.ENUM(
    "EASY", "MEDIUM", "HARD", name="challenge_difficulty", create_type=False
)


def upgrade() -> None:
    op.create_table(
        "duo_matches",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("room_code", sa.String(length=8), nullable=True),
        sa.Column("mode", duo_match_mode, nullable=False),
        sa.Column("status", duo_match_status, nullable=False, server_default="WAITING"),
        sa.Column("end_reason", duo_match_end_reason, nullable=True),
        sa.Column("player_one_id", sa.String(length=36), nullable=False),
        sa.Column("player_two_id", sa.String(length=36), nullable=True),
        sa.Column("winner_id", sa.String(length=36), nullable=True),
        sa.Column("player_one_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("player_two_score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("player_one_correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("player_two_correct", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("question_count", sa.Integer(), nullable=False),
        sa.Column("time_per_question", sa.Integer(), nullable=False),
        sa.Column("topic_id", sa.String(length=36), nullable=True),
        sa.Column("difficulty", challenge_difficulty, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["player_one_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["player_two_id"], ["users.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["winner_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["topic_id"], ["topics.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_duo_matches_room_code"), "duo_matches", ["room_code"], unique=True)
    op.create_index(
        op.f("ix_duo_matches_player_one_id"), "duo_matches", ["player_one_id"], unique=False
    )
    op.create_index(
        op.f("ix_duo_matches_player_two_id"), "duo_matches", ["player_two_id"], unique=False
    )
    op.create_index(op.f("ix_duo_matches_winner_id"), "duo_matches", ["winner_id"], unique=False)

    op.create_table(
        "duo_match_rounds",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("match_id", sa.String(length=36), nullable=False),
        sa.Column("round_index", sa.Integer(), nullable=False),
        sa.Column("challenge_id", sa.String(length=36), nullable=True),
        sa.Column("player_one_option_id", sa.String(length=36), nullable=True),
        sa.Column("player_two_option_id", sa.String(length=36), nullable=True),
        sa.Column("player_one_correct", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("player_two_correct", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("player_one_elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("player_two_elapsed_ms", sa.Integer(), nullable=True),
        sa.Column("player_one_points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("player_two_points", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["match_id"], ["duo_matches.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["challenge_id"], ["challenges.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["player_one_option_id"], ["challenge_options.id"], ondelete="SET NULL"
        ),
        sa.ForeignKeyConstraint(
            ["player_two_option_id"], ["challenge_options.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "match_id", "round_index", name="uq_duo_match_rounds_match_id_round_index"
        ),
    )
    op.create_index(
        op.f("ix_duo_match_rounds_match_id"), "duo_match_rounds", ["match_id"], unique=False
    )
    op.create_index(
        op.f("ix_duo_match_rounds_challenge_id"),
        "duo_match_rounds",
        ["challenge_id"],
        unique=False,
    )

    op.create_table(
        "duo_ratings",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("user_id", sa.String(length=36), nullable=False),
        sa.Column("rating", sa.Integer(), nullable=False, server_default="1000"),
        sa.Column("matches_played", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("wins", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("losses", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("draws", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("current_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("best_streak", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_duo_ratings_user_id"), "duo_ratings", ["user_id"], unique=True)
    op.create_index(op.f("ix_duo_ratings_rating"), "duo_ratings", ["rating"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_duo_ratings_rating"), table_name="duo_ratings")
    op.drop_index(op.f("ix_duo_ratings_user_id"), table_name="duo_ratings")
    op.drop_table("duo_ratings")

    op.drop_index(op.f("ix_duo_match_rounds_challenge_id"), table_name="duo_match_rounds")
    op.drop_index(op.f("ix_duo_match_rounds_match_id"), table_name="duo_match_rounds")
    op.drop_table("duo_match_rounds")

    op.drop_index(op.f("ix_duo_matches_winner_id"), table_name="duo_matches")
    op.drop_index(op.f("ix_duo_matches_player_two_id"), table_name="duo_matches")
    op.drop_index(op.f("ix_duo_matches_player_one_id"), table_name="duo_matches")
    op.drop_index(op.f("ix_duo_matches_room_code"), table_name="duo_matches")
    op.drop_table("duo_matches")

    duo_match_end_reason.drop(op.get_bind(), checkfirst=True)
    duo_match_status.drop(op.get_bind(), checkfirst=True)
    duo_match_mode.drop(op.get_bind(), checkfirst=True)
