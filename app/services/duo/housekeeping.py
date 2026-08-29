"""Periodic cleanup of duo state that nothing else will collect.

The match loop tears down its own room when a match ends, but two things can
still linger: a lobby whose host walked away without closing the socket, and a
queue entry nobody was ever paired with. A single background task sweeps both.
"""

import asyncio
import logging
from datetime import UTC, datetime

from app.db.session import AsyncSessionFactory
from app.models.duo.duo_match import DuoMatchStatus
from app.repository.duo.duo_match_repository import DuoMatchRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.season_repository import SeasonRepository
from app.schemas.duo.events import ServerEvent, envelope
from app.services.duo.match_runtime import (
    QUEUE_TIMEOUT_SECONDS,
    WAITING_ROOM_TTL_SECONDS,
    DuoEngine,
)
from app.services.duo.match_runtime import engine as default_engine
from app.services.game.energy_service import EnergyService
from app.services.game.season_service import roll_over_if_due

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60.0


async def abandon_orphaned_matches() -> int:
    """Close matches a previous process left running, and refund their cost.

    A match that charged its players and then died with the process owes that
    energy back. This is the only place that window is covered: the engine
    cannot refund from a cancelled task, and awaiting the database while being
    cancelled is exactly the fragile thing to avoid.
    """
    async with AsyncSessionFactory() as db:
        stranded = await DuoMatchRepository(db).abandon_orphaned()
        if stranded:
            await EnergyService(profiles=GameProfileRepository(db)).refund_match(
                stranded
            )
        # Two players per match, and a match with an empty second seat counts
        # once.
        return len(stranded)


async def roll_seasons() -> str | None:
    """Close a finished ladder season and open the next one.

    Ridden on the sweep that already runs every minute rather than given a cron
    of its own, because nothing else in this service needs a scheduler.
    """
    async with AsyncSessionFactory() as db:
        season = await roll_over_if_due(SeasonRepository(db), datetime.now(UTC))
        return season.code if season is not None else None


async def sweep(engine: DuoEngine | None = None) -> None:
    """One cleanup pass: time out stale queue entries, then close any
    waiting room nobody has joined for too long."""
    active = engine or default_engine
    registry = active.registry

    async with registry.lock:
        stale_entries = registry.stale_queue_entries(QUEUE_TIMEOUT_SECONDS)
        for entry in stale_entries:
            registry.dequeue(entry.user_id)

    for entry in stale_entries:
        try:
            await entry.websocket.send_json(envelope(ServerEvent.QUEUE_TIMEOUT, None))
        except Exception:  # noqa: BLE001 -- a dead socket needs no notification
            logger.debug("Could not notify %s of queue timeout", entry.user_id)

    for match in active.registry.all_matches():
        if (
            match.status is DuoMatchStatus.WAITING
            and registry.age_seconds(match) >= WAITING_ROOM_TTL_SECONDS
        ):
            logger.info("Closing idle duo room %s", match.room_code or match.match_id)
            await active.close_idle_room(match)

    opened = await roll_seasons()
    if opened is not None:
        logger.info("Opened ladder season %s", opened)


async def run_housekeeping(interval_seconds: float = SWEEP_INTERVAL_SECONDS) -> None:
    """Sweep forever. Cancelled on application shutdown."""
    while True:
        await asyncio.sleep(interval_seconds)
        try:
            await sweep()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Duo housekeeping sweep failed")
