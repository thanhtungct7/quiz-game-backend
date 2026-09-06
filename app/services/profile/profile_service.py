"""Assembling one player's whole profile out of the tables that hold pieces of it.

This is the read side only; nothing here writes. It is the same job
`player_card.py` does for a lobby row, widened to a whole screen -- and it
reuses that module's rules rather than restating them, so a player's level and
tier read the same on a profile card as they do on a match summary.

Everything derivable is derived: level from `total_exp`, tier from `rating`,
CEFR band from level, energy from the pair of columns that describe it. A
stored value is never trusted over the thing it was computed from, because a
profile screen is exactly where a stale cache would be noticed.
"""

from datetime import UTC, datetime

from app.core.config import Settings
from app.core.exceptions import UserNotFoundError
from app.models.auth.user import User
from app.models.duo.duo_rating import DEFAULT_RATING, DuoRating
from app.models.game.user_game_profile import UserGameProfile
from app.repository.profile.profile_stats_repository import (
    LearningTotals,
    ProfileStatsRepository,
)
from app.schemas.game.game import EnergyRead
from app.schemas.profile.profile import (
    LearningStatsRead,
    PublicProfileRead,
    PvpStatsRead,
    SelfProfileRead,
)
from app.services.auth.avatar_url import resolve_avatar_url
from app.services.game.cefr import (
    cefr_for_level,
    next_cefr_at_level,
    toeic_estimate_for_level,
)
from app.services.game.energy import MAX_ENERGY, current_energy, next_regen_at
from app.services.game.leveling import exp_for_level, exp_to_next_level, level_for_exp
from app.services.game.season import tier_for_rating


class ProfileService:
    def __init__(self, *, stats: ProfileStatsRepository, config: Settings) -> None:
        self.stats = stats
        self.config = config

    async def get_self(self, user_id: str) -> SelfProfileRead:
        """The caller's own profile, with the private half filled in."""
        user, profile, rating, totals = await self._gather(user_id)
        public = _public_read(
            user=user,
            profile=profile,
            rating=rating,
            totals=totals,
            config=self.config,
        )
        level = public.level
        total_exp = profile.total_exp if profile is not None else 0
        upcoming = next_cefr_at_level(level)
        return SelfProfileRead(
            **public.model_dump(),
            email=user.email,
            has_uploaded_avatar=user.avatar_file_id is not None,
            gold=profile.gold if profile is not None else 0,
            energy=_energy_read(profile),
            total_exp=total_exp,
            exp_for_current_level=exp_for_level(level),
            exp_for_next_level=exp_for_level(level + 1),
            exp_to_next_level=exp_to_next_level(total_exp),
            next_cefr=upcoming[0] if upcoming is not None else None,
            next_cefr_at_level=upcoming[1] if upcoming is not None else None,
        )

    async def get_public(self, user_id: str) -> PublicProfileRead:
        """Another player's profile, as seen from a leaderboard or a lobby."""
        user, profile, rating, totals = await self._gather(user_id)
        return _public_read(
            user=user,
            profile=profile,
            rating=rating,
            totals=totals,
            config=self.config,
        )

    async def _gather(
        self, user_id: str
    ) -> tuple[User, UserGameProfile | None, DuoRating | None, LearningTotals]:
        """The two round trips, in order. Sequential on purpose -- see the
        repository's note on why these must not be gathered concurrently."""
        found = await self.stats.identity_with_standing(user_id)
        if found is None:
            raise UserNotFoundError(user_id)
        user, profile, rating = found
        return user, profile, rating, await self.stats.learning_totals(user_id)


def _energy_read(profile: UserGameProfile | None) -> EnergyRead:
    """Energy as of right now, regenerated lazily rather than stored.

    Mirrors `GameService._energy_read`; the two must stay in step, which is
    cheap to hold because both are three calls into `energy.py` and neither
    owns a rule of its own. A player with no profile row yet has never spent
    anything, so their bar is full.
    """
    if profile is None:
        return EnergyRead(current=MAX_ENERGY, maximum=MAX_ENERGY, next_regen_at=None)
    now = datetime.now(UTC)
    return EnergyRead(
        current=current_energy(profile.energy, profile.energy_updated_at, now),
        maximum=MAX_ENERGY,
        next_regen_at=next_regen_at(profile.energy, profile.energy_updated_at, now),
    )


def _pvp_read(rating: DuoRating | None) -> PvpStatsRead:
    """The ladder half. A player who has never finished a match gets the
    default rating and zeroes -- the same answer `DuoService.get_stats` gives,
    so the two endpoints cannot disagree about a new account."""
    if rating is None:
        return PvpStatsRead(
            rating=DEFAULT_RATING,
            tier=tier_for_rating(DEFAULT_RATING),
            matches_played=0,
            wins=0,
            losses=0,
            draws=0,
            win_rate=0.0,
        )
    played = rating.matches_played
    return PvpStatsRead(
        rating=rating.rating,
        tier=tier_for_rating(rating.rating),
        matches_played=played,
        wins=rating.wins,
        losses=rating.losses,
        draws=rating.draws,
        win_rate=round(rating.wins / played * 100, 1) if played else 0.0,
    )


def _learning_read(totals: LearningTotals) -> LearningStatsRead:
    attempted, mastered, attempts = totals
    return LearningStatsRead(
        challenges_attempted=attempted,
        challenges_mastered=mastered,
        total_attempts=attempts,
        # Answers that landed over answers given. Rounded like `win_rate`.
        accuracy=round(mastered / attempts * 100, 1) if attempts else 0.0,
    )


def _public_read(
    *,
    user: User,
    profile: UserGameProfile | None,
    rating: DuoRating | None,
    totals: LearningTotals,
    config: Settings,
) -> PublicProfileRead:
    """The half of a profile anyone may see."""
    level = level_for_exp(profile.total_exp) if profile is not None else 1
    return PublicProfileRead(
        id=user.id,
        username=user.username,
        bio=user.bio,
        avatar_url=resolve_avatar_url(user, config),
        joined_at=user.created_at,
        level=level,
        cefr=cefr_for_level(level),
        toeic_estimate=toeic_estimate_for_level(level),
        class_code=profile.class_code if profile is not None else None,
        day_streak=profile.day_streak if profile is not None else 0,
        best_day_streak=profile.best_day_streak if profile is not None else 0,
        pvp=_pvp_read(rating),
        learning=_learning_read(totals),
    )
