from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth.user import User
from app.models.duo.duo_rating import DuoRating


class DuoRatingRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def get_by_user(self, user_id: str) -> DuoRating | None:
        statement = select(DuoRating).where(DuoRating.user_id == user_id)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_or_create(self, user_id: str) -> DuoRating:
        existing = await self.get_by_user(user_id)
        if existing is not None:
            return existing
        rating = DuoRating(user_id=user_id)
        self.db.add(rating)
        await self.db.commit()
        await self.db.refresh(rating)
        return rating

    async def save(self, rating: DuoRating, data: dict[str, object]) -> DuoRating:
        for field, value in data.items():
            setattr(rating, field, value)
        await self.db.commit()
        await self.db.refresh(rating)
        return rating

    async def save_many(self, ratings: list[DuoRating]) -> None:
        self.db.add_all(ratings)
        await self.db.commit()

    async def leaderboard(self, limit: int, offset: int = 0) -> list[tuple[DuoRating, User]]:
        statement = (
            select(DuoRating, User)
            .join(User, User.id == DuoRating.user_id)
            .where(DuoRating.matches_played > 0)
            .order_by(DuoRating.rating.desc(), DuoRating.wins.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(statement)
        return [(rating, user) for rating, user in result.all()]

    async def rank_of(self, user_id: str) -> int | None:
        """1-based position on the leaderboard, or None if never played."""
        own = await self.get_by_user(user_id)
        if own is None or own.matches_played == 0:
            return None
        statement = (
            select(func.count())
            .select_from(DuoRating)
            .where(DuoRating.matches_played > 0, DuoRating.rating > own.rating)
        )
        result = await self.db.execute(statement)
        return int(result.scalar_one()) + 1

    async def ratings_by_user_ids(self, user_ids: list[str]) -> dict[str, int]:
        if not user_ids:
            return {}
        statement = select(DuoRating).where(DuoRating.user_id.in_(user_ids))
        result = await self.db.execute(statement)
        return {record.user_id: record.rating for record in result.scalars().all()}

    async def users_by_ids(self, user_ids: list[str]) -> dict[str, User]:
        if not user_ids:
            return {}
        statement = select(User).where(User.id.in_(user_ids))
        result = await self.db.execute(statement)
        return {user.id: user for user in result.scalars().all()}
