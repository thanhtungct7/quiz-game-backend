from datetime import date
from uuid import uuid4

from sqlalchemy import Date, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class UserDailyActivity(Base):
    """One row per user per active day, in the learner's own timezone.

    The streak counter on the profile is the fast path; this is the record it
    is derived from, and what a calendar view would read.
    """

    __tablename__ = "user_daily_activity"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "activity_date", name="uq_user_daily_activity_user_id_activity_date"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    activity_date: Mapped[date] = mapped_column(Date, nullable=False)
    lessons_completed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    matches_played: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
