"""Achievements: a catalog of thresholds, and who has crossed which of them.

Deliberately not an event log. Every achievement is a `(metric, threshold)`
pair measured against counters the system already keeps -- level, day streak,
questions mastered, matches won -- so unlocking one is a comparison rather than
something that has to be observed at the moment it happens.

That buys three things. Nothing new is written on the hot path of a match. A
player who was already past a threshold before the achievement existed unlocks
it the first time anything syncs, rather than having to earn it again. And
adding an achievement later is a row in `services/game/catalog.py`, not a code
change -- the same rule the class, item and monster catalogs follow.

The cost is that only monotonic, currently-true facts can be achievements: "hit
level 20" works, "won three matches in one day" does not, because nothing here
remembers a day. If that is ever wanted it needs its own counter first, and the
counter is the honest place for it to live.
"""

from datetime import UTC, datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AchievementCategory(StrEnum):
    """Which tab of the profile an achievement belongs under."""

    PROGRESSION = "PROGRESSION"
    LEARNING = "LEARNING"
    PVP = "PVP"
    PVE = "PVE"


class AchievementMetric(StrEnum):
    """The counter an achievement is measured against.

    Every value here must be readable from `AchievementMetrics`; a metric with
    nothing behind it would be an achievement that can never unlock, so the
    mapping is exhaustive and a test holds it that way.
    """

    LEVEL = "LEVEL"
    DAY_STREAK = "DAY_STREAK"
    BEST_DAY_STREAK = "BEST_DAY_STREAK"
    CHALLENGES_MASTERED = "CHALLENGES_MASTERED"
    TOTAL_ATTEMPTS = "TOTAL_ATTEMPTS"
    LESSONS_COMPLETED = "LESSONS_COMPLETED"
    PVP_WINS = "PVP_WINS"
    PVP_RATING = "PVP_RATING"
    PVP_BEST_STREAK = "PVP_BEST_STREAK"
    BATTLES_WON = "BATTLES_WON"


class Achievement(Base):
    """One thing worth doing, and the number that says it was done."""

    __tablename__ = "achievements"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    category: Mapped[AchievementCategory] = mapped_column(
        Enum(AchievementCategory, name="achievement_category"), nullable=False
    )
    metric: Mapped[AchievementMetric] = mapped_column(
        Enum(AchievementMetric, name="achievement_metric"), nullable=False
    )
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    # A short name for the artwork, resolved client-side the way `art_code` is
    # for monsters: the catalog can grow an achievement before the icon exists,
    # and an unknown code should draw a fallback rather than nothing.
    icon_code: Mapped[str] = mapped_column(String(32), nullable=False, default="")
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # Hidden ones are kept out of the "still to earn" list and only appear once
    # they are unlocked, so a surprise stays one.
    is_hidden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class UserAchievement(Base):
    """That a player has crossed a threshold, and when they did.

    The unique constraint is what makes syncing idempotent: a sync inserts
    everything currently earned and lets the constraint drop what was already
    there, so running it twice cannot double-award or re-date anything.
    """

    __tablename__ = "user_achievements"
    __table_args__ = (UniqueConstraint("user_id", "achievement_id", name="uq_user_achievement"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    achievement_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("achievements.id", ondelete="CASCADE"), nullable=False
    )
    unlocked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
