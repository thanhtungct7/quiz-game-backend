"""The aggregated profile: one payload gathering what six tables know.

Two shapes, and the split between them is the whole point of this module.
`PublicProfileRead` is what anyone may see about anyone -- the same discipline
`PlayerCardRead` follows, so `email` and `hashed_password` cannot reach a
lobby or a leaderboard by accident. `SelfProfileRead` extends it with the
things only the account holder may read.

The direction of that inheritance matters: extending Public with Self means a
field added to Self can never leak into Public, while the reverse would leak
by default. `DuoMatchDetail` extends `DuoMatchSummary` for the same reason.
"""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from app.models.game.achievement import AchievementCategory
from app.schemas.game.game import EnergyRead
from app.services.game.cefr import CefrBand
from app.services.game.combat_stats import StatSourceKind
from app.services.game.season import RankTier


class AchievementRead(BaseModel):
    """One unlocked achievement, as a card shows it.

    `icon_code` is resolved to artwork client-side the way a monster's
    `art_code` is: the catalog can grow an achievement before anyone draws an
    icon for it, and an unknown code should fall back rather than blank out.
    """

    code: str
    name: str
    description: str
    category: AchievementCategory
    icon_code: str
    unlocked_at: datetime


class AchievementProgressRead(AchievementRead):
    """The same, plus how far off it is -- for the ones not yet earned.

    `unlocked_at` is null until it is earned, and `current` is clamped to the
    threshold so a finished bar cannot read as 130%.
    """

    unlocked_at: datetime | None = None  # type: ignore[assignment]
    threshold: int
    current: int
    unlocked: bool


class AchievementListRead(BaseModel):
    unlocked_count: int
    total: int
    items: list[AchievementProgressRead]


class CombatStatsRead(BaseModel):
    """What a player brings into a fight, already resolved.

    Class, equipment and the daily streak are added up server-side, so this is
    what a match will really use rather than a base a client has to assemble.

    `atk` is the damage a correct answer deals, not the multiplier that
    produced it: `damage_permille` carries the exact figure for anyone who
    needs it, but a card compares this against `hp` and the multiplier cannot
    be compared against anything. `defence` is flat damage off each blow.
    """

    hp: int
    atk: int
    defence: int
    mana: int
    damage_permille: int


class StatSourceRead(BaseModel):
    """One line of "where did this number come from".

    Every figure is a delta and the lines add up to [CombatStatsRead] exactly,
    including the case where equipment ceilings clipped the total -- see
    `combat_stats.resolve`.
    """

    kind: StatSourceKind
    code: str
    label: str
    hp: int
    atk: int
    defence: int
    mana: int


class CombatBreakdownRead(BaseModel):
    total: CombatStatsRead
    sources: list[StatSourceRead]


class LearningStatsRead(BaseModel):
    """What the study side of the account adds up to.

    `accuracy` is answers that landed over answers given, in percent -- the
    same shape and rounding as `DuoStatsRead.win_rate`, so the two read alike
    on a card that shows both.

    It is honest about what is stored rather than precise: `user_challenge_
    progress` keeps the best-ever outcome per question, not a log of answers,
    so a mastered question counts as exactly one correct answer among its
    attempts. That understates a player who re-drilled something they already
    knew, and it never overstates.
    """

    challenges_attempted: int
    challenges_mastered: int
    total_attempts: int
    accuracy: float


class PvpStatsRead(BaseModel):
    """The duo ladder, flattened. `tier` is derived from `rating` rather than
    stored, so the two cannot disagree."""

    rating: int
    tier: RankTier
    matches_played: int
    wins: int
    losses: int
    draws: int
    win_rate: float


class PublicProfileRead(BaseModel):
    """What one player may see about another.

    `class_code` rather than the class name, for `PlayerCardRead`'s reason:
    names come from `GET /game/classes`, which the client already caches, and
    carrying the name here would turn a leaderboard into fifty catalog lookups.
    """

    id: str
    username: str | None
    bio: str | None
    avatar_url: str | None
    joined_at: datetime

    level: int
    cefr: CefrBand
    toeic_estimate: int

    class_code: str | None
    day_streak: int
    best_day_streak: int

    pvp: PvpStatsRead
    learning: LearningStatsRead
    combat: CombatStatsRead

    # Reserved for the character and achievement modules, which do not exist
    # yet. They are declared now, and answer null/empty until those modules
    # land, so the clients drawing this card do not have to be rewritten when
    # they do -- only this service gains a source.
    title: str | None = None
    companion_character: str | None = None
    skin_code: str | None = None

    # The three most recently unlocked, which is what the overview tab draws.
    # Not the whole list: this payload is fetched for every row of a
    # leaderboard, and a player with fifty badges would make that fifty times
    # heavier for a card that shows three. The full list is its own endpoint.
    featured_achievements: list[AchievementRead] = Field(default_factory=list)
    total_achievements_unlocked: int = 0


class SelfProfileRead(PublicProfileRead):
    """Everything above, plus what only the account holder may read."""

    email: EmailStr
    has_uploaded_avatar: bool
    gold: int
    energy: EnergyRead

    total_exp: int
    exp_for_current_level: int
    exp_for_next_level: int
    exp_to_next_level: int

    # None once the top band is reached.
    next_cefr: CefrBand | None
    next_cefr_at_level: int | None
