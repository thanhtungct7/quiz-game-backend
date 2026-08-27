"""Periodic cleanup of duo state that nothing else will collect.

The match loop tears down its own room when a match ends, but two things can
still linger: a lobby whose host walked away without closing the socket, and a
queue entry nobody was ever paired with. A single background task sweeps both.
"""

import asyncio
import logging

from app.db.session import AsyncSessionFactory
from app.models.duo.duo_match import DuoMatchStatus
from app.repository.duo.duo_match_repository import DuoMatchRepository
from app.schemas.duo.events import ServerEvent, envelope
from app.services.duo.match_runtime import (
    QUEUE_TIMEOUT_SECONDS,
    WAITING_ROOM_TTL_SECONDS,
    DuoEngine,
)
from app.services.duo.match_runtime import engine as default_engine

logger = logging.getLogger(__name__)

SWEEP_INTERVAL_SECONDS = 60.0


async def abandon_orphaned_matches() -> int:
    """Close matches a previous process left running.

    Live state is in memory, so a restart makes every IN_PROGRESS match
    unreachable; without this they would sit in history forever.
    """
    async with AsyncSessionFactory() as db:
        return await DuoMatchRepository(db).abandon_orphaned()


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
