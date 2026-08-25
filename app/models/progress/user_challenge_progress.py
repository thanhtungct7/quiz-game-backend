from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserChallengeProgress(Base):
    """Best-ever outcome of a user attempting a single challenge."""

    __tablename__ = "user_challenge_progress"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "challenge_id", name="uq_user_challenge_progress_user_id_challenge_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    challenge_id: Mapped[str] = mapped_column(
        ForeignKey("challenges.id", ondelete="CASCADE"), nullable=False, index=True
    )
    lesson_id: Mapped[str] = mapped_column(
        ForeignKey("lessons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    last_selected_option_id: Mapped[str | None] = mapped_column(
        ForeignKey("challenge_options.id", ondelete="SET NULL"), nullable=True
    )
    mastered: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    attempts_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    mastered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
