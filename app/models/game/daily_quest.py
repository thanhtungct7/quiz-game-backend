"""Daily quests: the catalog, the four each player draws per day, and the chests
their activity points open.

Unlike achievements, a quest *is* remembered per day -- "win a match today" is
exactly the kind of fact `models/game/achievement.py` says it cannot hold -- so
it has its own rows, keyed by the day they were drawn for.

`quest_type` and `difficulty` are plain strings rather than database enums, the
same call `notification_dispatches.kind` makes: adding a kind of quest is a row
in `services/game/quest_catalog.py` and a branch in `QuestEvent`, not a
migration. The values are `daily_quests.QuestType` / `QuestDifficulty`.
"""

from datetime import UTC, date, datetime
from uuid import uuid4

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DailyQuestTemplate(Base):
    """One quest that can be drawn, with its target and what it pays."""

    __tablename__ = "daily_quest_templates"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    quest_type: Mapped[str] = mapped_column(String(32), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(100), nullable=False)
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    activity_points: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_gold: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reward_exp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )


class UserDailyQuest(Base):
    """One of a player's quests for one day.

    The target, points and rewards are copied off the template when the quest
    is drawn, so retuning the catalog mid-day never moves a goal a player is
    already halfway to. The title is not copied: renaming a quest should show.

    `completed_at` / `claimed_at` stand in for the `is_completed` / `is_claimed`
    flags of the original spec. A timestamp answers the flag's question and
    also when, which is what lets the end of a battle report "the quests you
    finished during it". The unique constraint is what makes two requests
    racing to draw the same day's set harmless.
    """

    __tablename__ = "user_daily_quests"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "template_id",
            "quest_date",
            name="uq_user_daily_quests_user_id_template_id_quest_date",
        ),
        Index("ix_user_daily_quests_user_id_quest_date", "user_id", "quest_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    template_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("daily_quest_templates.id", ondelete="CASCADE"), nullable=False
    )
    quest_date: Mapped[date] = mapped_column(Date, nullable=False)
    quest_type: Mapped[str] = mapped_column(String(32), nullable=False)
    difficulty: Mapped[str] = mapped_column(String(16), nullable=False)
    target: Mapped[int] = mapped_column(Integer, nullable=False)
    activity_points: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_gold: Mapped[int] = mapped_column(Integer, nullable=False)
    reward_exp: Mapped[int] = mapped_column(Integer, nullable=False)
    slot: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )


class UserDailyActivityChest(Base):
    """That a player opened one activity chest on one day.

    One row per opened chest rather than the spec's JSON list of claimed
    milestones: the unique constraint then *is* the double-claim guard, the
    same way `loot_grants` guards a match's chest. The day's points are not
    stored at all -- they are the sum over the quests finished that day, and a
    stored copy could only drift from it.
    """

    __tablename__ = "user_daily_activity_chests"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "quest_date",
            "milestone",
            name="uq_user_daily_activity_chests_user_id_quest_date_milestone",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quest_date: Mapped[date] = mapped_column(Date, nullable=False)
    milestone: Mapped[int] = mapped_column(Integer, nullable=False)
    claimed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
