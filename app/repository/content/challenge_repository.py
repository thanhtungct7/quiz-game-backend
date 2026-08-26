from sqlalchemy import func, select
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

    async def list_by_lesson(
        self, lesson_id: str, *, limit: int | None = None, offset: int = 0
    ) -> list[Challenge]:
        statement = (
            select(Challenge)
            .where(Challenge.lesson_id == lesson_id)
            .options(selectinload(Challenge.options), selectinload(Challenge.passage))
            .order_by(Challenge.order_index)
        )
        if limit is not None:
            statement = statement.limit(limit).offset(offset)
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def count_by_lesson(self, lesson_id: str) -> int:
        """How many challenges a lesson holds, without loading any of them.

        Progress recomputation runs on every answer check, so it must never pull
        the lesson's rows (and their options) just to call len() on them.
        """
        statement = (
            select(func.count())
            .select_from(Challenge)
            .where(Challenge.lesson_id == lesson_id)
        )
        result = await self.db.execute(statement)
        return int(result.scalar_one())

    async def count_by_lessons(self, lesson_ids: list[str]) -> dict[str, int]:
        """Challenge counts for many lessons in one query. Lessons with no
        challenges are absent from the result rather than mapped to 0."""
        if not lesson_ids:
            return {}
        statement = (
            select(Challenge.lesson_id, func.count())
            .where(Challenge.lesson_id.in_(lesson_ids))
            .group_by(Challenge.lesson_id)
        )
        result = await self.db.execute(statement)
        return {lesson_id: int(count) for lesson_id, count in result.all()}

    async def list_by_lesson_filtered(
        self,
        lesson_id: str,
        topic_ids: list[str] | None = None,
        difficulties: list[ChallengeDifficulty] | None = None,
        limit: int | None = None,
    ) -> list[Challenge]:
        """Candidate pool for a lesson quiz.

        `limit` caps the pool at the database, drawing it at random. Bank lessons
        hold tens of thousands of challenges, so an unbounded pool would load the
        whole thing just to sample ten questions out of it.
        """
        statement = (
            select(Challenge)
            .where(Challenge.lesson_id == lesson_id)
            .options(selectinload(Challenge.options), selectinload(Challenge.passage))
        )
        if topic_ids:
            statement = statement.where(Challenge.topic_id.in_(topic_ids))
        if difficulties:
            statement = statement.where(Challenge.difficulty.in_(difficulties))
        if limit is not None:
            statement = statement.order_by(func.random()).limit(limit)
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def list_random_filtered(
        self,
        limit: int,
        topic_ids: list[str] | None = None,
        difficulties: list[ChallengeDifficulty] | None = None,
    ) -> list[Challenge]:
        """Draw challenges at random from the whole bank, not from one lesson.

        Used by duo matches, where both players share a single question set that
        is not tied to any lesson the two of them happen to have in common.
        """
        statement = (
            select(Challenge)
            .options(selectinload(Challenge.options), selectinload(Challenge.passage))
            .order_by(func.random())
            .limit(limit)
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

    async def list_by_ids(self, challenge_ids: list[str]) -> list[Challenge]:
        if not challenge_ids:
            return []
        statement = (
            select(Challenge)
            .where(Challenge.id.in_(challenge_ids))
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
