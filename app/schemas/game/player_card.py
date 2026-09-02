"""The public face of a player.

Identity lives in `users`, standing lives in `user_game_profiles` and the duo
ladder. A card is the read-only projection of the three that other players are
allowed to see — never the row itself, so `hashed_password` and `email` cannot
reach a lobby by accident.

Everywhere the client draws *somebody* — the duo lobby, a live match, a history
row — it draws this, so the same player never shows up with a different set of
facts in two places.
"""

from pydantic import BaseModel

from app.services.game.season import RankTier


class PlayerCardRead(BaseModel):
    id: str
    username: str | None
    avatar_url: str | None
    rating: int
    # Derived from `rating` rather than stored, so the two cannot disagree.
    tier: RankTier
    level: int
    # The class code, not its name: names come from `GET /game/classes`, which
    # the client already caches to draw the class picker. Carrying the name
    # here would turn a twenty-row history page into twenty catalog lookups.
    class_code: str | None
    day_streak: int
