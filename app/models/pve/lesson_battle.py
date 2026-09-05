from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class BattleStatus(StrEnum):
    IN_PROGRESS = "IN_PROGRESS"
    WON = "WON"
    LOST = "LOST"
    ABANDONED = "ABANDONED"


class BattleEndReason(StrEnum):
    MONSTER_DOWN = "MONSTER_DOWN"
    PLAYER_DOWN = "PLAYER_DOWN"
    OUT_OF_QUESTIONS = "OUT_OF_QUESTIONS"
    LEFT = "LEFT"
    CANCELLED = "CANCELLED"


class LessonBattle(Base):
    """One run at the monster guarding one lesson.

    The row is written when the battle starts, so a process that dies mid-fight
    leaves a trace `abandon_orphaned_battles()` can close on the next boot.

    There is deliberately no per-round table: every answer already lands in
    `user_challenge_progress` through the ordinary study path, and a second
    record of the same thing would be a second thing to keep in step.
    """

    __tablename__ = "lesson_battles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson_id: Mapped[str] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # By code rather than by id: the catalog row can be retired, the history
    # of what was fought must not move with it.
    monster_code: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[BattleStatus] = mapped_column(
        Enum(BattleStatus, name="battle_status"),
        nullable=False,
        default=BattleStatus.IN_PROGRESS,
        server_default=BattleStatus.IN_PROGRESS.value,
    )
    end_reason: Mapped[BattleEndReason | None] = mapped_column(
        Enum(BattleEndReason, name="battle_end_reason"), nullable=True
    )
    player_hp_left: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    monster_hp_left: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    rounds_played: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    correct_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    best_combo: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    exp_awarded: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    gold_awarded: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # True only for the run that first cleared this lesson, which is decided by
    # the gold ledger rather than by a column of its own.
    first_clear: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
