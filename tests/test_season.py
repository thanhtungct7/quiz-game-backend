from app.services.duo.rating import RATING_FLOOR
from app.services.game.season import (
    DEFAULT_SEASON_RATING,
    TIER_FLOORS,
    RankTier,
    soft_reset,
    tier_floor,
    tier_for_rating,
)


def test_every_tier_starts_exactly_at_its_floor() -> None:
    for tier, floor in TIER_FLOORS:
        assert tier_for_rating(floor) is tier
        if floor > 0:
            assert tier_for_rating(floor - 1) is not tier


def test_the_ladder_runs_bronze_to_master() -> None:
    assert tier_for_rating(0) is RankTier.BRONZE
    assert tier_for_rating(1000) is RankTier.BRONZE
    assert tier_for_rating(1200) is RankTier.SILVER
    assert tier_for_rating(1400) is RankTier.GOLD
    assert tier_for_rating(1600) is RankTier.PLATINUM
    assert tier_for_rating(1800) is RankTier.DIAMOND
    assert tier_for_rating(2400) is RankTier.MASTER


def test_a_rating_below_the_bottom_still_has_a_tier() -> None:
    assert tier_for_rating(-500) is RankTier.BRONZE


def test_tier_floor_round_trips() -> None:
    for tier, floor in TIER_FLOORS:
        assert tier_floor(tier) == floor


def test_a_soft_reset_pulls_toward_the_middle_from_both_directions() -> None:
    high = soft_reset(2000)
    low = soft_reset(400)
    assert DEFAULT_SEASON_RATING < high < 2000
    assert 400 < low < DEFAULT_SEASON_RATING


def test_the_middle_survives_a_reset_unchanged() -> None:
    assert soft_reset(DEFAULT_SEASON_RATING) == DEFAULT_SEASON_RATING


def test_a_reset_never_drops_below_the_rating_floor() -> None:
    assert soft_reset(0) >= RATING_FLOOR
    assert soft_reset(-9999) >= RATING_FLOOR


def test_a_reset_keeps_the_ordering_between_players() -> None:
    ratings = [800, 1000, 1300, 1700, 2100]
    reset = [soft_reset(rating) for rating in ratings]
    assert reset == sorted(reset)


def test_a_strong_season_still_starts_the_next_one_above_average() -> None:
    assert tier_for_rating(soft_reset(2100)) is not RankTier.BRONZE
