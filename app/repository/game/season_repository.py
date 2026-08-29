from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth.user import User
from app.models.game.season import GameSeason, SeasonRating


class SeasonRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def active(self) -> GameSeason | None:
        statement = (
            select(GameSeason)
            .where(GameSeason.is_active.is_(True))
            .order_by(GameSeason.starts_at.desc())
            .limit(1)
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_by_code(self, code: str) -> GameSeason | None:
        statement = select(GameSeason).where(GameSeason.code == code)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def create(self, season: GameSeason) -> GameSeason:
        self.db.add(season)
        await self.db.commit()
        await self.db.refresh(season)
        return season

    async def close(self, season: GameSeason, ended_at: datetime) -> None:
        season.is_active = False
        season.ends_at = min(season.ends_at, ended_at)
        await self.db.commit()

    async def rating(self, season_id: str, user_id: str) -> SeasonRating | None:
        statement = select(SeasonRating).where(
            SeasonRating.season_id == season_id, SeasonRating.user_id == user_id
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def get_or_create_rating(
        self, season_id: str, user_id: str, *, opening_rating: int
    ) -> SeasonRating:
        existing = await self.rating(season_id, user_id)
        if existing is not None:
            return existing
        record = SeasonRating(
            season_id=season_id,
            user_id=user_id,
            rating=opening_rating,
            peak_rating=opening_rating,
        )
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def save_rating(
        self, record: SeasonRating, data: dict[str, object]
    ) -> SeasonRating:
        for field, value in data.items():
            setattr(record, field, value)
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def leaderboard(
        self, season_id: str, limit: int, offset: int = 0
    ) -> list[tuple[SeasonRating, User]]:
        statement = (
            select(SeasonRating, User)
            .join(User, User.id == SeasonRating.user_id)
            .where(SeasonRating.season_id == season_id, SeasonRating.matches_played > 0)
            .order_by(SeasonRating.rating.desc(), SeasonRating.wins.desc())
            .limit(limit)
            .offset(offset)
        )
        result = await self.db.execute(statement)
        return [(rating, user) for rating, user in result.all()]
