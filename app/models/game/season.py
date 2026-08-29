from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

DEFAULT_SEASON_RATING = 1000


class GameSeason(Base):
    """A ladder period. Exactly one is active at a time."""

    __tablename__ = "game_seasons"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true", index=True
    )


class SeasonRating(Base):
    """A player's standing within one season.

    Separate from `duo_ratings`, which stays the all-time rating and is never
    reset. This is the projection the ladder resets each season.
    """

    __tablename__ = "season_ratings"
    __table_args__ = (
        UniqueConstraint(
            "season_id", "user_id", name="uq_season_ratings_season_id_user_id"
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    season_id: Mapped[str] = mapped_column(
        ForeignKey("game_seasons.id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_SEASON_RATING,
        server_default=str(DEFAULT_SEASON_RATING),
        index=True,
    )
    peak_rating: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=DEFAULT_SEASON_RATING,
        server_default=str(DEFAULT_SEASON_RATING),
    )
    matches_played: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    wins: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    losses: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    draws: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
