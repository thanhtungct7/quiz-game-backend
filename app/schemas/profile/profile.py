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

from app.schemas.game.game import EnergyRead
from app.services.game.cefr import CefrBand
from app.services.game.season import RankTier


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

    # Reserved for the character and achievement modules, which do not exist
    # yet. They are declared now, and answer null/empty until those modules
    # land, so the clients drawing this card do not have to be rewritten when
    # they do -- only this service gains a source.
    title: str | None = None
    companion_character: str | None = None
    skin_code: str | None = None
    achievements: list[str] = Field(default_factory=list)


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
