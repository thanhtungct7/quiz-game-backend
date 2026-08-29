from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import Date, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# Mirrors energy.MAX_ENERGY; declared here so the model layer does not depend
# on a service. tests/test_energy.py asserts the two never drift apart.
MAX_ENERGY = 5

STARTING_GOLD = 0


class UserGameProfile(Base):
    """A user's standing in the game layer: experience, level and gold.

    One row per user, created lazily the first time anything needs it.

    `total_exp` is the source of truth. `level` is a cache recomputed from it on
    every write, kept as a column only so the leaderboard and profile reads do
    not have to derive it in SQL.
    """

    __tablename__ = "user_game_profiles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True
    )
    total_exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    level: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    gold: Mapped[int] = mapped_column(
        Integer, nullable=False, default=STARTING_GOLD, server_default=str(STARTING_GOLD)
    )
    # NULL until the player picks one; they can still fight on default stats.
    class_code: Mapped[str | None] = mapped_column(
        ForeignKey("game_classes.code", ondelete="SET NULL"), nullable=True
    )
    class_chosen_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Regenerates lazily: nothing runs in the background, the pair below is
    # interpreted against the current time on every read.
    energy: Mapped[int] = mapped_column(
        Integer, nullable=False, default=MAX_ENERGY, server_default=str(MAX_ENERGY)
    )
    energy_updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    day_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    best_day_streak: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    last_active_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
