"""The player card: the one shape every screen uses to draw somebody else.

All pure -- a card is assembled from rows that are already in hand, never by
going back to the database, which is what lets a twenty-row history page cost
one profile query instead of twenty.
"""

from app.models.game.user_game_profile import UserGameProfile
from app.services.game.leveling import exp_for_level
from app.services.game.player_card import (
    PlayerStanding,
    build_player_card,
    standing_of,
)
from app.services.game.season import RankTier


def _profile(*, total_exp: int = 0, level: int = 1, class_code: str | None = None,
             day_streak: int = 0) -> UserGameProfile:
    return UserGameProfile(
        user_id="user-1",
        total_exp=total_exp,
        level=level,
        class_code=class_code,
        day_streak=day_streak,
    )


def test_a_player_with_no_game_profile_still_gets_a_card() -> None:
    """An account older than the game layer must not become a hole in the
    lobby: it shows up at level 1 with no class, not as an error."""
    standing = standing_of(None, 1200)

    card = build_player_card(
        user_id="user-1", username="tung", avatar_url=None, standing=standing
    )

    assert card.level == 1
    assert card.class_code is None
    assert card.day_streak == 0
    assert card.rating == 1200


def test_the_level_on_a_card_comes_from_experience_not_the_cached_column() -> None:
    """`level` is a cache; `total_exp` is the truth. A stale cache must not
    reach another player's screen."""
    standing = standing_of(_profile(total_exp=exp_for_level(7), level=3), 1000)

    assert standing.level == 7


def test_the_tier_is_derived_from_the_rating() -> None:
    gold = build_player_card(
        user_id="user-1",
        username=None,
        avatar_url=None,
        standing=PlayerStanding(rating=1350),
    )
    bronze = build_player_card(
        user_id="user-2",
        username=None,
        avatar_url=None,
        standing=PlayerStanding(rating=900),
    )

    assert gold.tier is RankTier.GOLD
    assert bronze.tier is RankTier.BRONZE


def test_a_card_carries_the_class_and_streak_a_profile_holds() -> None:
    standing = standing_of(
        _profile(total_exp=exp_for_level(4), class_code="MAGE", day_streak=12), 1000
    )

    card = build_player_card(
        user_id="user-1",
        username="tung",
        avatar_url="https://example.test/avatar.png",
        standing=standing,
    )

    assert card.level == 4
    assert card.class_code == "MAGE"
    assert card.day_streak == 12
    assert card.username == "tung"
    assert card.avatar_url == "https://example.test/avatar.png"
