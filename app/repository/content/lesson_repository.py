from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content.lesson import Lesson
from app.models.content.unit import Unit


class LessonRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, lesson: Lesson) -> Lesson:
        self.db.add(lesson)
        await self.db.commit()
        await self.db.refresh(lesson)
        return lesson

    async def list_by_unit(self, unit_id: str, *, include_bank: bool = False) -> list[Lesson]:
        """Lessons of one unit. Bank lessons are excluded unless asked for -- they
        are storage for leftover imported questions, not steps on the path."""
        statement = select(Lesson).where(Lesson.unit_id == unit_id)
        if not include_bank:
            statement = statement.where(Lesson.is_bank.is_(False))
        result = await self.db.execute(statement.order_by(Lesson.order_index))
        return list(result.scalars().all())

    async def list_path_by_course(self, course_id: str) -> list[Lesson]:
        """Every non-bank lesson of a course in path order, in one query.

        Feeds the course-tree endpoint, which replaces the client's old
        one-request-per-unit walk.
        """
        statement = (
            select(Lesson)
            .join(Unit, Lesson.unit_id == Unit.id)
            .where(Unit.course_id == course_id, Lesson.is_bank.is_(False))
            .order_by(Unit.order_index, Lesson.order_index)
        )
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
