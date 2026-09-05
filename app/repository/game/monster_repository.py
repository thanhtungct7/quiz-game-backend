from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game.monster import Monster


class MonsterRepository:
    """Reads and seeds the monster catalog."""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_monsters(self, *, include_inactive: bool = False) -> list[Monster]:
        statement = select(Monster)
        if not include_inactive:
            statement = statement.where(Monster.is_active.is_(True))
        result = await self.db.execute(
            statement.order_by(Monster.sort_order, Monster.code)
        )
        return list(result.scalars().all())

    async def get_by_code(self, code: str) -> Monster | None:
        statement = select(Monster).where(Monster.code == code)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def upsert_monster(self, code: str, data: dict[str, object]) -> Monster:
        existing = await self.get_by_code(code)
        if existing is None:
            existing = Monster(code=code)
            self.db.add(existing)
        for field, value in data.items():
            setattr(existing, field, value)
        await self.db.commit()
        await self.db.refresh(existing)
        return existing
