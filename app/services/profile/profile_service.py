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
from app.models.game.achievement import Achievement, UserAchievement
from app.models.game.user_game_profile import UserGameProfile
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.profile.profile_stats_repository import (
    LearningTotals,
    ProfileStatsRepository,
)
from app.schemas.game.game import EnergyRead
from app.schemas.profile.profile import (
    AchievementListRead,
    AchievementProgressRead,
    AchievementRead,
    CombatBreakdownRead,
    CombatStatsRead,
    LearningStatsRead,
    PublicProfileRead,
    PvpStatsRead,
    SelfProfileRead,
    StatSourceRead,
)
from app.services.auth.avatar_url import resolve_avatar_url
from app.services.game.achievement_service import AchievementService
from app.services.game.achievements import reading_of
from app.services.game.cefr import (
    cefr_for_level,
    next_cefr_at_level,
    toeic_estimate_for_level,
)
from app.services.game.combat_stats import Build, ClassPart, class_part, item_part, resolve
from app.services.game.energy import MAX_ENERGY, current_energy, next_regen_at
from app.services.game.leveling import exp_for_level, exp_to_next_level, level_for_exp
from app.services.game.season import tier_for_rating

# How many badges the overview tab has room for. The most recent ones, because
# "what I just earned" is the question a profile is opened to answer; the whole
# shelf is a tab of its own.
FEATURED_ACHIEVEMENTS = 3

# What a player holds, newest first -- the shape `AchievementRepository.unlocked`
# returns and this module passes around.
Unlocked = list[tuple[UserAchievement, Achievement]]


class ProfileService:
    def __init__(
        self,
        *,
        stats: ProfileStatsRepository,
        catalog: CatalogRepository,
        items: ItemRepository,
        achievements: AchievementService,
        config: Settings,
    ) -> None:
        self.stats = stats
        # The same two repositories a match reads its build from. Borrowed
        # rather than reimplemented: a card that showed different numbers to
        # the ones the engine fights on would be worse than showing none.
        self.catalog = catalog
        self.items = items
        self.achievements = achievements
        self.config = config

    async def get_self(self, user_id: str) -> SelfProfileRead:
        """The caller's own profile, with the private half filled in."""
        user, profile, rating, totals, build, held = await self._gather(user_id)
        public = _public_read(
            user=user,
            profile=profile,
            rating=rating,
            totals=totals,
            build=build,
            held=held,
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
        user, profile, rating, totals, build, held = await self._gather(user_id)
        return _public_read(
            user=user,
            profile=profile,
            rating=rating,
            totals=totals,
            build=build,
            held=held,
            config=self.config,
        )

    async def get_achievements(self, user_id: str) -> AchievementListRead:
        """The whole shelf: what has been earned, and how far off the rest is.

        Syncs first, and this is the one read path that does. Opening the tab is
        a deliberate act by the player whose data it is, it is nowhere near the
        card's latency budget, and it is what lets an account older than an
        achievement pick it up without having to go and earn it again.

        Hidden achievements are left out until they are unlocked, so a surprise
        stays one.
        """
        await self.achievements.sync(user_id)
        catalog = await self.achievements.catalog()
        held = {row.id: unlocked for unlocked, row in await self.achievements.unlocked(user_id)}
        metrics = await self.achievements.metrics(user_id)

        items = [
            AchievementProgressRead(
                code=row.code,
                name=row.name,
                description=row.description,
                category=row.category,
                icon_code=row.icon_code,
                threshold=row.threshold,
                # Clamped, so a finished bar cannot read as 130%.
                current=min(reading_of(metrics, row.metric), row.threshold),
                unlocked=row.id in held,
                unlocked_at=held[row.id].unlocked_at if row.id in held else None,
            )
            for row in catalog
            if row.id in held or not row.is_hidden
        ]
        return AchievementListRead(
            unlocked_count=sum(1 for item in items if item.unlocked),
            total=len(items),
            items=items,
        )

    async def get_combat_breakdown(self, user_id: str) -> CombatBreakdownRead:
        """Where each of the four numbers on the card came from.

        Its own endpoint rather than a field on the profile: it is only read
        when a player opens the modal that asks, and the card itself has a
        latency budget to keep.
        """
        found = await self.stats.identity_with_standing(user_id)
        if found is None:
            raise UserNotFoundError(user_id)
        _user, profile, _rating = found
        build = await self._build(profile)
        return CombatBreakdownRead(
            total=_combat_read(build),
            sources=[
                StatSourceRead(
                    kind=source.kind,
                    code=source.code,
                    label=source.label,
                    hp=source.hp,
                    atk=source.atk,
                    defence=source.defence,
                    mana=source.mana,
                )
                for source in build.sources
            ],
        )

    async def _gather(
        self, user_id: str
    ) -> tuple[
        User, UserGameProfile | None, DuoRating | None, LearningTotals, Build, Unlocked
    ]:
        """The round trips, in order. Sequential on purpose -- see the
        repository's note on why these must not be gathered concurrently.

        Five at most, and only three for an account that has never opened the
        game: the class and the equipment are not asked for when there is no
        game profile to hang them on.

        Note that the achievement read is a read. Syncing belongs to the paths
        that can have changed a counter -- settling a match, finishing a lesson,
        opening the achievement tab -- and a card must never be one of them.
        """
        found = await self.stats.identity_with_standing(user_id)
        if found is None:
            raise UserNotFoundError(user_id)
        user, profile, rating = found
        totals = await self.stats.learning_totals(user_id)
        build = await self._build(profile)
        return user, profile, rating, totals, build, await self.achievements.unlocked(user_id)

    async def _build(self, profile: UserGameProfile | None) -> Build:
        """The player's stat block, resolved without writing anything.

        Deliberately not `LoadoutBuilder.build()`, which grants starter skills
        as a side effect -- reading a profile must never write -- and which
        loads a skill bar this card does not draw. What is shared instead is the
        arithmetic itself, `combat_stats.resolve`, so the two cannot drift.
        """
        if profile is None:
            return resolve(base=None)

        base: ClassPart | None = None
        if profile.class_code is not None:
            class_row = await self.catalog.get_class(profile.class_code)
            if class_row is not None and class_row.is_active:
                base = class_part(class_row)

        return resolve(
            base=base,
            equipment=[
                item_part(item)
                for _worn, item in await self.items.equipment(profile.user_id)
                if item.is_active
            ],
            day_streak=profile.day_streak,
        )


def _achievement_read(row: Achievement, unlocked: UserAchievement) -> AchievementRead:
    return AchievementRead(
        code=row.code,
        name=row.name,
        description=row.description,
        category=row.category,
        icon_code=row.icon_code,
        unlocked_at=unlocked.unlocked_at,
    )


def _combat_read(build: Build) -> CombatStatsRead:
    """The resolved stat block, as the card reads it."""
    stats = build.stats
    return CombatStatsRead(
        hp=stats.hp,
        atk=stats.atk,
        defence=stats.defence,
        mana=stats.mana,
        damage_permille=stats.damage_permille,
    )


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
    build: Build,
    held: Unlocked,
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
        combat=_combat_read(build),
        # Already newest-first out of the repository, so this is a slice rather
        # than a sort -- the order is a property of the query, not a decision
        # each caller gets to make differently.
        featured_achievements=[
            _achievement_read(row, unlocked)
            for unlocked, row in held[:FEATURED_ACHIEVEMENTS]
        ],
        total_achievements_unlocked=len(held),
    )

