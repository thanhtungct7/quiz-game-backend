"""Opening and rolling over ladder seasons."""

from datetime import UTC, datetime, timedelta

from app.models.game.season import GameSeason
from app.repository.game.season_repository import SeasonRepository
from app.services.game.season import (
    DEFAULT_SEASON_RATING,
    SEASON_LENGTH_DAYS,
    soft_reset,
)


def _season_code(started: datetime) -> str:
    return f"S{started:%Y%m}"


async def ensure_active_season(seasons: SeasonRepository) -> GameSeason:
    """The running season, opening the first one if none exists yet."""
    active = await seasons.active()
    if active is not None:
        return active
    now = datetime.now(UTC)
    return await seasons.create(
        GameSeason(
            code=_season_code(now),
            name=f"Mùa {now:%m/%Y}",
            starts_at=now,
            ends_at=now + timedelta(days=SEASON_LENGTH_DAYS),
            is_active=True,
        )
    )


async def roll_over_if_due(seasons: SeasonRepository, now: datetime) -> GameSeason | None:
    """Close a finished season and open the next one.

    Returns the new season when a rollover happened, otherwise None. Driven
    from the housekeeping sweep that already runs every minute, so seasons need
    no cron of their own.

    Standings are not migrated here: a player's opening rating for the new
    season is computed from their previous one the first time they play in it
    (`soft_reset`), which keeps the rollover O(1) instead of touching every row
    of a table that only grows.
    """
    active = await seasons.active()
    if active is None:
        return await ensure_active_season(seasons)
    if now < active.ends_at:
        return None

    await seasons.close(active, now)
    started = active.ends_at
    return await seasons.create(
        GameSeason(
            code=_season_code(started),
            name=f"Mùa {started:%m/%Y}",
            starts_at=started,
            ends_at=started + timedelta(days=SEASON_LENGTH_DAYS),
            is_active=True,
        )
    )


def opening_rating(previous_rating: int | None) -> int:
    """What a player starts a new season on, given their last one."""
    if previous_rating is None:
        return DEFAULT_SEASON_RATING
    return soft_reset(previous_rating)
