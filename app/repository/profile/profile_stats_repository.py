"""The reads behind an aggregated profile, kept to two round trips.

A profile card draws on `users`, `user_game_profiles`, `duo_ratings` and
`user_challenge_progress`. Asking for those one at a time is four round trips
for one screen, and the public card has a 100ms budget to meet, so the first
three are joined into a single statement here.

Note what this does *not* do: it does not fan the queries out with
`asyncio.gather`. An `AsyncSession` is not safe for concurrent use, and
overlapping statements on one session fail in ways that only show up under
load. Fewer round trips is the way to make this fast; parallel ones are not.
"""

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth.user import User
from app.models.duo.duo_rating import DuoRating
from app.models.game.user_game_profile import UserGameProfile
from app.models.progress.user_challenge_progress import UserChallengeProgress

# What `learning_totals` answers: challenges attempted, of those mastered, and
# answers given across all of them.
LearningTotals = tuple[int, int, int]


class ProfileStatsRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def identity_with_standing(
        self, user_id: str
    ) -> tuple[User, UserGameProfile | None, DuoRating | None] | None:
        """The user together with their game profile and ladder rating.

        Both joins are outer, and that is not defensive coding: the two rows
        are created lazily, so an account older than the game layer -- or one
        that has never finished a match -- genuinely has neither. `None` here
        means "has not started yet", which the service turns into the same
        defaults `standing_of` already uses. Only a missing *user* is an error.
        """
        statement = (
            select(User, UserGameProfile, DuoRating)
            .outerjoin(UserGameProfile, UserGameProfile.user_id == User.id)
            .outerjoin(DuoRating, DuoRating.user_id == User.id)
            .where(User.id == user_id)
        )
        row = (await self.db.execute(statement)).first()
        if row is None:
            return None
        user: User = row[0]
        profile: UserGameProfile | None = row[1]
        rating: DuoRating | None = row[2]
        return user, profile, rating

    async def learning_totals(self, user_id: str) -> LearningTotals:
        """Three aggregates over one index scan rather than three queries.

        `count(...) FILTER (WHERE mastered)` counts the mastered rows in the
        same pass as the total; `coalesce` covers the sum being NULL for a
        player who has never answered anything.
        """
        statement = (
            select(
                func.count(),
                func.count().filter(UserChallengeProgress.mastered),
                func.coalesce(func.sum(UserChallengeProgress.attempts_count), 0),
            )
            .select_from(UserChallengeProgress)
            .where(UserChallengeProgress.user_id == user_id)
        )
        row = (await self.db.execute(statement)).one()
        return int(row[0]), int(row[1]), int(row[2])
