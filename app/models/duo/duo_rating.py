from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DEFAULT_RATING = 1000


class DuoRating(Base):
    """Aggregate PvP standing of a user, updated once per finished match."""

    __tablename__ = "duo_ratings"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    # Indexed because the leaderboard orders by it.
    rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_RATING,
        server_default=str(DEFAULT_RATING),
        index=True,
    )
    matches_played: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    draws: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    current_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    best_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
