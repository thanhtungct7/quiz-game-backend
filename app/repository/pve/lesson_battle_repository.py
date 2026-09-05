from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.pve.lesson_battle import BattleEndReason, BattleStatus, LessonBattle


class LessonBattleRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, battle: LessonBattle) -> LessonBattle:
        self.db.add(battle)
        await self.db.commit()
        await self.db.refresh(battle)
        return battle

    async def get_by_id(self, battle_id: str) -> LessonBattle | None:
        return await self.db.get(LessonBattle, battle_id)

    async def finish(self, battle: LessonBattle, data: dict[str, object]) -> LessonBattle:
        for field, value in data.items():
            setattr(battle, field, value)
        await self.db.commit()
        await self.db.refresh(battle)
        return battle

    async def list_for_user(
        self, user_id: str, limit: int, offset: int
    ) -> list[LessonBattle]:
        statement = (
            select(LessonBattle)
            .where(
                LessonBattle.user_id == user_id,
                LessonBattle.status != BattleStatus.IN_PROGRESS,
            )
            .order_by(LessonBattle.started_at.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def has_won(self, user_id: str, lesson_id: str) -> bool:
        """Whether this player has ever cleared this lesson's monster."""
        statement = (
            select(LessonBattle.id)
            .where(
                LessonBattle.user_id == user_id,
                LessonBattle.lesson_id == lesson_id,
                LessonBattle.status == BattleStatus.WON,
            )
            .limit(1)
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none() is not None

    async def cleared_lessons(self, user_id: str, lesson_ids: list[str]) -> set[str]:
        """Which of these lessons the player has already cleared, in one query.

        Feeds the course map, which asks about every lesson of a course at once.
        """
        if not lesson_ids:
            return set()
        statement = (
            select(LessonBattle.lesson_id)
            .where(
                LessonBattle.user_id == user_id,
                LessonBattle.lesson_id.in_(lesson_ids),
                LessonBattle.status == BattleStatus.WON,
            )
            .distinct()
        )
        result = await self.db.execute(statement)
        return set(result.scalars().all())

    async def best_result(self, user_id: str, lesson_id: str) -> LessonBattle | None:
        """The player's most recent finished run at this lesson."""
        statement = (
            select(LessonBattle)
            .where(
                LessonBattle.user_id == user_id,
                LessonBattle.lesson_id == lesson_id,
                LessonBattle.status != BattleStatus.IN_PROGRESS,
            )
            .order_by(LessonBattle.started_at.desc())
            .limit(1)
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def abandon_orphaned(self) -> int:
        """Close battles left IN_PROGRESS by a previous process.

        Battle state lives in memory, so a restart makes every running battle
        unreachable. Nothing is refunded because nothing was charged: a lost
        battle costs no energy, and the answers already given were written to
        progress as they happened.
        """
        statement = (
            update(LessonBattle)
            .where(LessonBattle.status == BattleStatus.IN_PROGRESS)
            .values(
                status=BattleStatus.ABANDONED,
                end_reason=BattleEndReason.CANCELLED,
                finished_at=func.now(),
            )
            .returning(LessonBattle.id)
        )
        result = await self.db.execute(statement)
        await self.db.commit()
        return len(result.all())

    async def claim_for_settlement(self, battle_id: str, status: BattleStatus) -> bool:
        """Move the battle out of IN_PROGRESS, and say whether this caller did it.

        Settling writes to several tables and commits more than once, so a
        retry can re-enter it partway through. This is the serialization point:
        exactly one caller sees True, and only that caller may hand out
        rewards. The rest of the result is written afterwards by `finish`.
        """
        statement = (
            update(LessonBattle)
            .where(
                LessonBattle.id == battle_id,
                LessonBattle.status == BattleStatus.IN_PROGRESS,
            )
            .values(status=status)
            .returning(LessonBattle.id)
        )
        result = await self.db.execute(statement)
        claimed = result.scalar_one_or_none() is not None
        await self.db.commit()
        return claimed
