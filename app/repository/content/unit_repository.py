from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.unit import Unit


class UnitRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, unit: Unit) -> Unit:
        self.db.add(unit)
        await self.db.commit()
        await self.db.refresh(unit)
        return unit

    async def list_by_course(self, course_id: str) -> list[Unit]:
        statement = select(Unit).where(Unit.course_id == course_id).order_by(Unit.order_index)
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, unit_id: str) -> Unit | None:
        return await self.db.get(Unit, unit_id)

    async def get_by_course_and_order(self, course_id: str, order_index: int) -> Unit | None:
        statement = select(Unit).where(
            Unit.course_id == course_id, Unit.order_index == order_index
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def update(self, unit: Unit, data: dict[str, object]) -> Unit:
        for field, value in data.items():
            setattr(unit, field, value)
        await self.db.commit()
        await self.db.refresh(unit)
        return unit

    async def delete(self, unit: Unit) -> None:
        await self.db.delete(unit)
        await self.db.commit()
