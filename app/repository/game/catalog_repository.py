from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content.course import Course
from app.models.content.unit import Unit
from app.models.game.game_class import GameClass
from app.models.game.skill import Skill


class CatalogRepository:
    """Reads and seeds the class / skill catalog."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_classes(self) -> list[GameClass]:
        statement = (
            select(GameClass)
            .where(GameClass.is_active.is_(True))
            .order_by(GameClass.sort_order, GameClass.code)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def get_class(self, code: str) -> GameClass | None:
        statement = select(GameClass).where(GameClass.code == code)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def list_skills(self) -> list[Skill]:
        statement = (
            select(Skill)
            .where(Skill.is_active.is_(True))
            .order_by(Skill.sort_order, Skill.code)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def get_skill(self, skill_id: str) -> Skill | None:
        statement = select(Skill).where(Skill.id == skill_id)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def skills_by_codes(self, codes: list[str]) -> dict[str, Skill]:
        if not codes:
            return {}
        statement = select(Skill).where(Skill.code.in_(codes))
        result = await self.db.execute(statement)
        return {skill.code: skill for skill in result.scalars().all()}

    async def upsert_class(self, code: str, data: dict[str, object]) -> GameClass:
        existing = await self.get_class(code)
        if existing is None:
            existing = GameClass(code=code)
            self.db.add(existing)
        for field, value in data.items():
            setattr(existing, field, value)
        await self.db.commit()
        await self.db.refresh(existing)
        return existing

    async def upsert_skill(self, code: str, data: dict[str, object]) -> Skill:
        statement = select(Skill).where(Skill.code == code)
        result = await self.db.execute(statement)
        existing = result.scalar_one_or_none()
        if existing is None:
            existing = Skill(code=code)
            self.db.add(existing)
        for field, value in data.items():
            setattr(existing, field, value)
        await self.db.commit()
        await self.db.refresh(existing)
        return existing

    async def first_unit_ids(self, limit: int) -> list[str]:
        """The opening units of the learn path, in the order a student meets them."""
        statement = (
            select(Unit.id)
            .join(Course, Course.id == Unit.course_id)
            .order_by(Course.title, Unit.order_index)
            .limit(limit)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())
