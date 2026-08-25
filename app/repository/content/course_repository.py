from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content.course import Course


class CourseRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, course: Course) -> Course:
        self.db.add(course)
        await self.db.commit()
        await self.db.refresh(course)
        return course

    async def list_all(self) -> list[Course]:
        result = await self.db.execute(select(Course))
        return list(result.scalars().all())

    async def get_by_id(self, course_id: str) -> Course | None:
        return await self.db.get(Course, course_id)

    async def update(self, course: Course, data: dict[str, object]) -> Course:
        for field, value in data.items():
            setattr(course, field, value)
        await self.db.commit()
        await self.db.refresh(course)
        return course

    async def delete(self, course: Course) -> None:
        await self.db.delete(course)
        await self.db.commit()
