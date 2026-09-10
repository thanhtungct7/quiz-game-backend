"""The reads and writes behind achievements.

Two shapes of query live here and they are deliberately different. The catalog
and the unlocked rows are ordinary lookups. `metrics` is not: it is every
counter an achievement can be measured against, gathered in two round trips
rather than one per metric, because a sync runs at the end of every match and a
metric added later must not quietly cost another query.
"""

from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth.user import User
from app.models.duo.duo_rating import DEFAULT_RATING, DuoRating
from app.models.game.achievement import Achievement, UserAchievement
from app.models.game.user_game_profile import UserGameProfile
from app.models.progress.user_challenge_progress import UserChallengeProgress
from app.models.progress.user_lesson_progress import LessonProgressStatus, UserLessonProgress
from app.models.pve.lesson_battle import BattleStatus, LessonBattle
from app.services.game.achievements import AchievementMetrics
from app.services.game.leveling import level_for_exp


class AchievementRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert_achievement(self, code: str, data: dict[str, object]) -> Achievement:
        """Same upsert-on-code the other catalogs use: safe on every startup,
        and it never deletes, so an achievement somebody already holds keeps
        its row even after it is retired with `is_active = false`."""
        existing = await self.get_by_code(code)
        if existing is None:
            existing = Achievement(code=code)
            self.db.add(existing)
        for field, value in data.items():
            setattr(existing, field, value)
        await self.db.commit()
        await self.db.refresh(existing)
        return existing

    async def get_by_code(self, code: str) -> Achievement | None:
        statement = select(Achievement).where(Achievement.code == code)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def list_active(self) -> list[Achievement]:
        statement = (
            select(Achievement)
            .where(Achievement.is_active.is_(True))
            .order_by(Achievement.sort_order, Achievement.code)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def unlocked(self, user_id: str) -> list[tuple[UserAchievement, Achievement]]:
        """What this player holds, newest first.

        Ordered here rather than by the caller because "the three most recent"
        is what a profile card shows, and the order it wants is a property of
        the query rather than a decision the card gets to make differently.
        """
        statement = (
            select(UserAchievement, Achievement)
            .join(Achievement, Achievement.id == UserAchievement.achievement_id)
            .where(UserAchievement.user_id == user_id)
            .order_by(UserAchievement.unlocked_at.desc(), Achievement.sort_order)
        )
        result = await self.db.execute(statement)
        return [(held, achievement) for held, achievement in result.all()]

    async def grant(self, user_id: str, achievement_ids: list[str]) -> list[str]:
        """Record everything earned, and say which of it was new.

        One statement for the whole set, with the unique constraint deciding
        what was already held. That is what makes a sync idempotent: it offers
        every achievement the player currently qualifies for on every run, and
        only the first run of each actually writes.
        """
        if not achievement_ids:
            return []
        now = datetime.now(UTC)
        statement = (
            pg_insert(UserAchievement)
            .values(
                [
                    {
                        "id": str(uuid4()),
                        "user_id": user_id,
                        "achievement_id": achievement_id,
                        "unlocked_at": now,
                    }
                    for achievement_id in achievement_ids
                ]
            )
            .on_conflict_do_nothing(constraint="uq_user_achievement")
            .returning(UserAchievement.achievement_id)
        )
        result = await self.db.execute(statement)
        granted = [row[0] for row in result.all()]
        await self.db.commit()
        return granted

    async def metrics(self, user_id: str) -> AchievementMetrics:
        """Every counter, read at one moment, in two round trips.

        Level is recomputed from `total_exp` rather than read from the stored
        column, for the reason `player_card` and `profile_service` both give:
        the column is a cache, and an achievement handed out against a stale
        cache would be permanent.
        """
        standing = (
            select(UserGameProfile, DuoRating)
            .select_from(User)
            .outerjoin(UserGameProfile, UserGameProfile.user_id == User.id)
            .outerjoin(DuoRating, DuoRating.user_id == User.id)
            .where(User.id == user_id)
        )
        row = (await self.db.execute(standing)).first()
        profile: UserGameProfile | None = row[0] if row is not None else None
        rating: DuoRating | None = row[1] if row is not None else None

        mastered = (
            select(func.count())
            .select_from(UserChallengeProgress)
            .where(
                UserChallengeProgress.user_id == user_id,
                UserChallengeProgress.mastered,
            )
            .scalar_subquery()
        )
        attempts = (
            select(func.coalesce(func.sum(UserChallengeProgress.attempts_count), 0))
            .where(UserChallengeProgress.user_id == user_id)
            .scalar_subquery()
        )
        lessons = (
            select(func.count())
            .select_from(UserLessonProgress)
            .where(
                UserLessonProgress.user_id == user_id,
                UserLessonProgress.status == LessonProgressStatus.COMPLETED,
            )
            .scalar_subquery()
        )
        battles = (
            select(func.count())
            .select_from(LessonBattle)
            .where(LessonBattle.user_id == user_id, LessonBattle.status == BattleStatus.WON)
            .scalar_subquery()
        )
        counts = (await self.db.execute(select(mastered, attempts, lessons, battles))).one()

        return AchievementMetrics(
            level=level_for_exp(profile.total_exp) if profile is not None else 1,
            day_streak=profile.day_streak if profile is not None else 0,
            best_day_streak=profile.best_day_streak if profile is not None else 0,
            challenges_mastered=int(counts[0]),
            total_attempts=int(counts[1]),
            lessons_completed=int(counts[2]),
            battles_won=int(counts[3]),
            pvp_wins=rating.wins if rating is not None else 0,
            pvp_rating=rating.rating if rating is not None else DEFAULT_RATING,
            pvp_best_streak=rating.best_streak if rating is not None else 0,
        )
