from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.challenge_option import ChallengeOption


class ChallengeOptionRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def create(self, option: ChallengeOption) -> ChallengeOption:
        self.db.add(option)
        await self.db.commit()
        await self.db.refresh(option)
        return option

    async def list_by_challenge(self, challenge_id: str) -> list[ChallengeOption]:
        statement = (
            select(ChallengeOption)
            .where(ChallengeOption.challenge_id == challenge_id)
            .order_by(ChallengeOption.order_index)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def get_by_id(self, option_id: str) -> ChallengeOption | None:
        return await self.db.get(ChallengeOption, option_id)

    async def update(
        self, option: ChallengeOption, data: dict[str, object]
    ) -> ChallengeOption:
        for field, value in data.items():
            setattr(option, field, value)
        await self.db.commit()
        await self.db.refresh(option)
        return option

    async def delete(self, option: ChallengeOption) -> None:
        await self.db.delete(option)
        await self.db.commit()
