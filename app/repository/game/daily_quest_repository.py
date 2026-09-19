from datetime import date, datetime

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game.daily_quest import (
    DailyQuestTemplate,
    UserDailyActivityChest,
    UserDailyQuest,
)

QuestRow = tuple[UserDailyQuest, DailyQuestTemplate]

# Sessions are made with `expire_on_commit=False`, and the updates below skip
# session synchronisation, so a quest already loaded in this session would
# otherwise be handed back with the values it had before the update.
_FRESH = {"populate_existing": True}


class DailyQuestRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def rollback(self) -> None:
        await self.db.rollback()

    # --- catalog ------------------------------------------------------------------

    async def upsert_template(self, code: str, data: dict[str, object]) -> DailyQuestTemplate:
        """Upsert on code, like the other catalogs. Never deletes: a retired
        template keeps its row so yesterday's quests still have a title."""
        statement = select(DailyQuestTemplate).where(DailyQuestTemplate.code == code)
        existing = (await self.db.execute(statement)).scalar_one_or_none()
        if existing is None:
            existing = DailyQuestTemplate(code=code)
            self.db.add(existing)
        for field, value in data.items():
            setattr(existing, field, value)
        await self.db.commit()
        await self.db.refresh(existing)
        return existing

    async def active_templates(self) -> list[DailyQuestTemplate]:
        statement = (
            select(DailyQuestTemplate)
            .where(DailyQuestTemplate.is_active.is_(True))
            .order_by(DailyQuestTemplate.sort_order, DailyQuestTemplate.code)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    # --- a player's day -------------------------------------------------------------

    async def quests_for_day(self, user_id: str, day: date) -> list[QuestRow]:
        statement = (
            select(UserDailyQuest, DailyQuestTemplate)
            .join(DailyQuestTemplate, DailyQuestTemplate.id == UserDailyQuest.template_id)
            .where(UserDailyQuest.user_id == user_id, UserDailyQuest.quest_date == day)
            .order_by(UserDailyQuest.slot)
            .execution_options(**_FRESH)
        )
        result = await self.db.execute(statement)
        return [(quest, template) for quest, template in result.all()]

    async def insert_quests(self, rows: list[dict[str, object]]) -> None:
        """Insert a drawn set, letting the unique constraint drop whatever a
        concurrent request already inserted for the same day."""
        if not rows:
            return
        statement = (
            pg_insert(UserDailyQuest)
            .values(rows)
            .on_conflict_do_nothing(
                constraint="uq_user_daily_quests_user_id_template_id_quest_date"
            )
        )
        await self.db.execute(statement)
        await self.db.commit()

    async def advance(
        self,
        user_id: str,
        day: date,
        quest_type: str,
        amount: int,
        now: datetime,
        *,
        peak: bool,
    ) -> list[str]:
        """Move the day's unfinished quest of this type. Returns the ids of the
        quests this call finished.

        One statement, so two activities ending at once cannot lose each
        other's progress: the new value is computed from the stored one by the
        database, not read into Python first. Only unfinished quests are
        touched, which is also what makes "finished by this call" readable off
        the returned rows.
        """
        if peak:
            progressed = func.least(
                UserDailyQuest.target, func.greatest(UserDailyQuest.progress, amount)
            )
        else:
            progressed = func.least(UserDailyQuest.target, UserDailyQuest.progress + amount)
        statement = (
            update(UserDailyQuest)
            .where(
                UserDailyQuest.user_id == user_id,
                UserDailyQuest.quest_date == day,
                UserDailyQuest.quest_type == quest_type,
                UserDailyQuest.completed_at.is_(None),
            )
            .values(
                progress=progressed,
                completed_at=case((progressed >= UserDailyQuest.target, now), else_=None),
            )
            .returning(UserDailyQuest.id, UserDailyQuest.completed_at)
            .execution_options(synchronize_session=False)
        )
        result = await self.db.execute(statement)
        finished = [quest_id for quest_id, completed_at in result.all() if completed_at is not None]
        await self.db.commit()
        return finished

    async def completed_since(self, user_id: str, since: datetime) -> list[QuestRow]:
        statement = (
            select(UserDailyQuest, DailyQuestTemplate)
            .join(DailyQuestTemplate, DailyQuestTemplate.id == UserDailyQuest.template_id)
            .where(
                UserDailyQuest.user_id == user_id,
                UserDailyQuest.completed_at.is_not(None),
                UserDailyQuest.completed_at >= since,
            )
            .order_by(UserDailyQuest.completed_at, UserDailyQuest.slot)
            .execution_options(**_FRESH)
        )
        result = await self.db.execute(statement)
        return [(quest, template) for quest, template in result.all()]

    async def get_quest(self, user_id: str, quest_id: str) -> UserDailyQuest | None:
        statement = (
            select(UserDailyQuest)
            .where(UserDailyQuest.id == quest_id, UserDailyQuest.user_id == user_id)
            .execution_options(**_FRESH)
        )
        return (await self.db.execute(statement)).scalar_one_or_none()

    async def mark_claimed(self, user_id: str, quest_id: str, day: date, now: datetime) -> bool:
        """Claim a finished quest of today's set. False when it cannot be.

        The conditions live in the WHERE clause, so of two requests racing to
        claim the same quest the second finds `claimed_at` already set once the
        first commits, and claims nothing. Deliberately does not commit: the
        caller pays the reward in the same transaction.
        """
        statement = (
            update(UserDailyQuest)
            .where(
                UserDailyQuest.id == quest_id,
                UserDailyQuest.user_id == user_id,
                UserDailyQuest.quest_date == day,
                UserDailyQuest.completed_at.is_not(None),
                UserDailyQuest.claimed_at.is_(None),
            )
            .values(claimed_at=now)
            .returning(UserDailyQuest.id)
            .execution_options(synchronize_session=False)
        )
        return (await self.db.execute(statement)).scalar_one_or_none() is not None

    # --- chests ------------------------------------------------------------------------

    async def claimed_chests(self, user_id: str, day: date) -> set[int]:
        statement = select(UserDailyActivityChest.milestone).where(
            UserDailyActivityChest.user_id == user_id,
            UserDailyActivityChest.quest_date == day,
        )
        return set((await self.db.execute(statement)).scalars().all())

    async def open_chest(
        self, user_id: str, day: date, milestone: int, now: datetime
    ) -> str | None:
        """Record a chest as opened. Returns the new row's id, or None when it
        already was. Does not commit, for the same reason `mark_claimed` does not."""
        statement = (
            pg_insert(UserDailyActivityChest)
            .values(user_id=user_id, quest_date=day, milestone=milestone, claimed_at=now)
            .on_conflict_do_nothing(
                constraint="uq_user_daily_activity_chests_user_id_quest_date_milestone"
            )
            .returning(UserDailyActivityChest.id)
        )
        return (await self.db.execute(statement)).scalar_one_or_none()
