from datetime import datetime
from enum import StrEnum
from typing import TYPE_CHECKING
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base
from app.models.content.challenge import ChallengeDifficulty

if TYPE_CHECKING:
    from app.models.duo.duo_match_round import DuoMatchRound


class DuoMatchMode(StrEnum):
    RANDOM = "RANDOM"
    FRIEND = "FRIEND"


class DuoMatchStatus(StrEnum):
    WAITING = "WAITING"
    IN_PROGRESS = "IN_PROGRESS"
    FINISHED = "FINISHED"
    ABANDONED = "ABANDONED"
    CANCELLED = "CANCELLED"


class DuoMatchEndReason(StrEnum):
    COMPLETED = "COMPLETED"
    OPPONENT_LEFT = "OPPONENT_LEFT"
    OPPONENT_TIMEOUT = "OPPONENT_TIMEOUT"
    CANCELLED = "CANCELLED"


class DuoMatch(Base):
    """One 1v1 PvP match. A FINISHED match with a NULL winner_id is a draw."""

    __tablename__ = "duo_matches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    room_code: Mapped[str | None] = mapped_column(
        String(8), nullable=True, unique=True, index=True
    )
    mode: Mapped[DuoMatchMode] = mapped_column(
        Enum(DuoMatchMode, name="duo_match_mode"), nullable=False
    )
    status: Mapped[DuoMatchStatus] = mapped_column(
        Enum(DuoMatchStatus, name="duo_match_status"),
        nullable=False,
        default=DuoMatchStatus.WAITING,
        server_default=DuoMatchStatus.WAITING.value,
    )
    end_reason: Mapped[DuoMatchEndReason | None] = mapped_column(
        Enum(DuoMatchEndReason, name="duo_match_end_reason"), nullable=True
    )
    player_one_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    player_two_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True
    )
    winner_id: Mapped[str | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    player_one_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    player_two_score: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    player_one_correct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    player_two_correct: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    question_count: Mapped[int] = mapped_column(Integer, nullable=False)
    time_per_question: Mapped[int] = mapped_column(Integer, nullable=False)
    topic_id: Mapped[str | None] = mapped_column(
        ForeignKey("topics.id", ondelete="SET NULL"), nullable=True
    )
    difficulty: Mapped[ChallengeDifficulty | None] = mapped_column(
        Enum(ChallengeDifficulty, name="challenge_difficulty"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    rounds: Mapped[list["DuoMatchRound"]] = relationship(
        "DuoMatchRound",
        back_populates="match",
        cascade="all, delete-orphan",
        passive_deletes=True,
        order_by="DuoMatchRound.round_index",
    )
