"""Seasons and the rank ladder.

Pure functions with no I/O. A tier is always derived from a rating rather than
stored, so the two can never disagree.
"""

from enum import StrEnum

from app.services.duo.rating import RATING_FLOOR

DEFAULT_SEASON_RATING = 1000
SEASON_LENGTH_DAYS = 30

# How much of last season's rating carries over. A full reset throws away
# everything a player proved; no reset at all makes the ladder decorative.
CARRY_OVER_SHARE = 0.7


class RankTier(StrEnum):
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"
    PLATINUM = "PLATINUM"
    DIAMOND = "DIAMOND"
    MASTER = "MASTER"


# Ordered high to low, so the first match wins.
TIER_FLOORS: tuple[tuple[RankTier, int], ...] = (
    (RankTier.MASTER, 1900),
    (RankTier.DIAMOND, 1700),
    (RankTier.PLATINUM, 1500),
    (RankTier.GOLD, 1300),
    (RankTier.SILVER, 1100),
    (RankTier.BRONZE, 0),
)


def tier_for_rating(rating: int) -> RankTier:
    for tier, floor in TIER_FLOORS:
        if rating >= floor:
            return tier
    return RankTier.BRONZE


def tier_floor(tier: RankTier) -> int:
    return next(floor for candidate, floor in TIER_FLOORS if candidate is tier)


def soft_reset(rating: int) -> int:
    """Next season's opening rating, pulled part of the way back to the middle."""
    carried = rating * CARRY_OVER_SHARE + DEFAULT_SEASON_RATING * (1 - CARRY_OVER_SHARE)
    return max(RATING_FLOOR, round(carried))
