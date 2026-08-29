from datetime import date

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game.user_daily_activity import UserDailyActivity


class ActivityRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def record(
        self, user_id: str, day: date, *, lessons: int = 0, matches: int = 0
    ) -> None:
        """Add to today's tallies, creating the row on first activity."""
        statement = (
            pg_insert(UserDailyActivity)
            .values(
                user_id=user_id,
                activity_date=day,
                lessons_completed=lessons,
                matches_played=matches,
            )
            .on_conflict_do_update(
                constraint="uq_user_daily_activity_user_id_activity_date",
                set_={
                    "lessons_completed": UserDailyActivity.lessons_completed + lessons,
                    "matches_played": UserDailyActivity.matches_played + matches,
                },
            )
        )
        await self.db.execute(statement)
        await self.db.commit()

    async def recent(self, user_id: str, limit: int = 30) -> list[UserDailyActivity]:
        statement = (
            select(UserDailyActivity)
            .where(UserDailyActivity.user_id == user_id)
            .order_by(UserDailyActivity.activity_date.desc())
            .limit(limit)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())
