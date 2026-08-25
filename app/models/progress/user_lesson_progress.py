from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class LessonProgressStatus(StrEnum):
    NOT_STARTED = "NOT_STARTED"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"


class UserLessonProgress(Base):
    """Aggregate, per-user completion state of a lesson, derived from its
    challenges' UserChallengeProgress rows."""

    __tablename__ = "user_lesson_progress"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "lesson_id", name="uq_user_lesson_progress_user_id_lesson_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson_id: Mapped[str] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[LessonProgressStatus] = mapped_column(
        Enum(LessonProgressStatus, name="lesson_progress_status"),
        nullable=False,
        default=LessonProgressStatus.NOT_STARTED,
        server_default=LessonProgressStatus.NOT_STARTED.value,
    )
    correct_challenge_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    total_challenge_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
