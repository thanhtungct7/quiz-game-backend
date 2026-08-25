from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models.content.challenge import Challenge, ChallengeDifficulty


class ChallengeRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, challenge: Challenge) -> Challenge:
        self.db.add(challenge)
        await self.db.commit()
        await self.db.refresh(challenge, attribute_names=["options", "passage"])
        return challenge

    async def list_by_lesson(self, lesson_id: str) -> list[Challenge]:
        statement = (
            select(Challenge)
            .where(Challenge.lesson_id == lesson_id)
            .options(selectinload(Challenge.options), selectinload(Challenge.passage))
            .order_by(Challenge.order_index)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def list_by_lesson_filtered(
        self,
        lesson_id: str,
        topic_ids: list[str] | None = None,
        difficulties: list[ChallengeDifficulty] | None = None,
    ) -> list[Challenge]:
        statement = (
            select(Challenge)
            .where(Challenge.lesson_id == lesson_id)
            .options(selectinload(Challenge.options), selectinload(Challenge.passage))
        )
        if topic_ids:
            statement = statement.where(Challenge.topic_id.in_(topic_ids))
        if difficulties:
            statement = statement.where(Challenge.difficulty.in_(difficulties))
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def list_by_topic(self, topic_id: str) -> list[Challenge]:
        statement = (
            select(Challenge)
            .where(Challenge.topic_id == topic_id)
            .options(selectinload(Challenge.options), selectinload(Challenge.passage))
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def list_all(self) -> list[Challenge]:
        statement = select(Challenge).options(
            selectinload(Challenge.options), selectinload(Challenge.passage)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, challenge_id: str) -> Challenge | None:
        statement = (
            select(Challenge)
            .where(Challenge.id == challenge_id)
            .options(selectinload(Challenge.options), selectinload(Challenge.passage))
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_lesson_and_order(self, lesson_id: str, order_index: int) -> Challenge | None:
        statement = select(Challenge).where(
            Challenge.lesson_id == lesson_id, Challenge.order_index == order_index
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def update(self, challenge: Challenge, data: dict[str, object]) -> Challenge:
        for field, value in data.items():
            setattr(challenge, field, value)
        await self.db.commit()
        await self.db.refresh(challenge, attribute_names=["options", "passage"])
        return challenge

    async def delete(self, challenge: Challenge) -> None:
        await self.db.delete(challenge)
        await self.db.commit()
