from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.lesson import Lesson


class LessonRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, lesson: Lesson) -> Lesson:
        self.db.add(lesson)
        await self.db.commit()
        await self.db.refresh(lesson)
        return lesson

    async def list_by_unit(self, unit_id: str) -> list[Lesson]:
        statement = select(Lesson).where(Lesson.unit_id == unit_id).order_by(Lesson.order_index)
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, lesson_id: str) -> Lesson | None:
        return await self.db.get(Lesson, lesson_id)

    async def get_by_unit_and_order(self, unit_id: str, order_index: int) -> Lesson | None:
        statement = select(Lesson).where(
            Lesson.unit_id == unit_id, Lesson.order_index == order_index
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def update(self, lesson: Lesson, data: dict[str, object]) -> Lesson:
        for field, value in data.items():
            setattr(lesson, field, value)
        await self.db.commit()
        await self.db.refresh(lesson)
        return lesson

    async def delete(self, lesson: Lesson) -> None:
        await self.db.delete(lesson)
        await self.db.commit()
