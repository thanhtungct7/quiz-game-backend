"""Assembling a player card out of the rows it projects.

`PlayerStanding` is the half of the card that is not identity, kept as its own
value so the engine can carry it around a live match without dragging a
`User` or an ORM session along.
"""

from dataclasses import dataclass

from app.models.duo.duo_rating import DEFAULT_RATING
from app.models.game.user_game_profile import UserGameProfile
from app.schemas.game.player_card import PlayerCardRead
from app.services.game.leveling import level_for_exp
from app.services.game.season import tier_for_rating

DEFAULT_LEVEL = 1


@dataclass(frozen=True)
class PlayerStanding:
    """How far along a player is: ladder rating from `duo_ratings`, the rest
    from `user_game_profiles`.

    The defaults are what a player carries before either row exists — the same
    call `default_loadout` makes, for the same reason: an account older than
    the game layer must not turn into a hole in the lobby.
    """

    rating: int = DEFAULT_RATING
    level: int = DEFAULT_LEVEL
    class_code: str | None = None
    day_streak: int = 0


def standing_of(profile: UserGameProfile | None, rating: int = DEFAULT_RATING) -> PlayerStanding:
    """Flatten a game profile into a standing.

    Level is recomputed from `total_exp` rather than read from the cached
    `level` column, matching `LoadoutBuilder.build` — one source of truth for
    how much experience buys a level.
    """
    if profile is None:
        return PlayerStanding(rating=rating)
    return PlayerStanding(
        rating=rating,
        level=level_for_exp(profile.total_exp),
        class_code=profile.class_code,
        day_streak=profile.day_streak,
    )


def build_player_card(
    *,
    user_id: str,
    username: str | None,
    avatar_url: str | None,
    standing: PlayerStanding,
) -> PlayerCardRead:
    """`avatar_url` is the resolved URL from `resolve_avatar_url`, not the raw
    column — the same rule `build_user_read` follows."""
    return PlayerCardRead(
        id=user_id,
        username=username,
        avatar_url=avatar_url,
        rating=standing.rating,
        tier=tier_for_rating(standing.rating),
        level=standing.level,
        class_code=standing.class_code,
        day_streak=standing.day_streak,
    )
