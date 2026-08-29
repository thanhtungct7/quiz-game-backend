"""What finishing a lesson is worth outside the progress tables.

This is the link that makes studying the way out of an empty energy bar, and
the reason a study streak is a *study* streak rather than a play streak.
"""

from datetime import UTC, datetime

from app.repository.game.activity_repository import ActivityRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.services.game.energy import REVIEW_REFILL
from app.services.game.energy_service import EnergyService
from app.services.game.streak import streak_after, today_in_streak_tz


class LessonRewardService:
    def __init__(
        self,
        *,
        profiles: GameProfileRepository,
        activity: ActivityRepository,
    ) -> None:
        self.profiles = profiles
        self.activity = activity

    async def on_lesson_completed(self, user_id: str) -> None:
        """Called once, the first time a lesson reaches COMPLETED."""
        await EnergyService(profiles=self.profiles).grant_for_lesson(
            user_id, REVIEW_REFILL
        )

        now = datetime.now(UTC)
        today = today_in_streak_tz(now)
        await self.activity.record(user_id, today, lessons=1)

        profile = await self.profiles.get_or_create(user_id)
        streak = streak_after(profile.day_streak, profile.last_active_date, today)
        await self.profiles.save(
            profile,
            {
                "day_streak": streak,
                "best_day_streak": max(profile.best_day_streak, streak),
                "last_active_date": today,
                "updated_at": now,
            },
        )
