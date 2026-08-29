from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game.user_game_profile import UserGameProfile


class GameProfileRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_user(self, user_id: str) -> UserGameProfile | None:
        statement = select(UserGameProfile).where(UserGameProfile.user_id == user_id)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_or_create(self, user_id: str) -> UserGameProfile:
        existing = await self.get_by_user(user_id)
        if existing is not None:
            return existing
        profile = UserGameProfile(user_id=user_id)
        self.db.add(profile)
        await self.db.commit()
        await self.db.refresh(profile)
        return profile

    async def save(self, profile: UserGameProfile, data: dict[str, object]) -> UserGameProfile:
        for field, value in data.items():
            setattr(profile, field, value)
        await self.db.commit()
        await self.db.refresh(profile)
        return profile

    async def profiles_by_user_ids(self, user_ids: list[str]) -> dict[str, UserGameProfile]:
        if not user_ids:
            return {}
        statement = select(UserGameProfile).where(UserGameProfile.user_id.in_(user_ids))
        result = await self.db.execute(statement)
        return {record.user_id: record for record in result.scalars().all()}
