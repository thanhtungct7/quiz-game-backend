"""Keeping a player's unlocked achievements in step with what they have done.

There is no moment an achievement is "earned" in this design -- there is only a
counter that has passed a threshold and a row that has not been written yet. So
this is a reconciliation rather than an event handler, and the interesting
property is that running it more often than necessary is free of consequence.

Where it runs: at the end of a match and of a lesson battle, when a lesson is
completed, and when a player opens their own achievement list. The last of
those is what lets an account that predates an achievement pick it up without
having to go and earn it again.

Where it deliberately does *not* run: reading a profile card. That path is a
pure read with a latency budget, and a card is not the place to discover that
the database needed writing.
"""

from app.models.game.achievement import Achievement, UserAchievement
from app.repository.game.achievement_repository import AchievementRepository
from app.services.game.achievements import AchievementMetrics, unlocked_codes


class AchievementService:
    def __init__(self, achievements: AchievementRepository) -> None:
        self.achievements = achievements

    async def sync(self, user_id: str) -> list[Achievement]:
        """Grant everything this player now qualifies for. Returns what was new.

        Reads before it writes, so the ordinary case -- a match that unlocked
        nothing -- costs no write at all. The insert is still guarded by the
        unique constraint rather than trusting that read, because two matches
        settling at once would otherwise race each other into a duplicate.
        """
        catalog = await self.achievements.list_active()
        if not catalog:
            return []

        metrics = await self.achievements.metrics(user_id)
        held = {row.code for _unlocked, row in await self.achievements.unlocked(user_id)}
        missing = unlocked_codes(catalog, metrics) - held
        if not missing:
            return []

        by_code = {row.code: row for row in catalog}
        granted = set(
            await self.achievements.grant(
                user_id, [by_code[code].id for code in sorted(missing)]
            )
        )
        return [row for row in catalog if row.id in granted]

    async def metrics(self, user_id: str) -> AchievementMetrics:
        """The readings behind the list, for showing progress on locked rows."""
        return await self.achievements.metrics(user_id)

    async def catalog(self) -> list[Achievement]:
        """Everything on offer, in display order."""
        return await self.achievements.list_active()

    async def unlocked(self, user_id: str) -> list[tuple[UserAchievement, Achievement]]:
        """What this player holds, newest first. Read without syncing, so a
        profile card stays a pure read."""
        return await self.achievements.unlocked(user_id)
