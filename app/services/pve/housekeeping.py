"""Periodic cleanup of PvE state that nothing else will collect.

A battle normally tears itself down when it ends or when the socket drops. Two
things can still linger: a battle whose task died without ending the fight, and
rows left IN_PROGRESS by a process that stopped mid-fight. The first is swept
here; the second is closed once at start-up.

Nothing is ever refunded, because a battle charges nothing to begin with.
"""

import logging

from app.db.session import AsyncSessionFactory
from app.repository.pve.lesson_battle_repository import LessonBattleRepository
from app.services.pve.battle_runtime import BATTLE_TTL_SECONDS, BattleEngine
from app.services.pve.battle_runtime import engine as default_engine

logger = logging.getLogger(__name__)


async def abandon_orphaned_battles() -> int:
    """Close battles a previous process left running.

    Battle state lives in memory, so a restart makes every running battle
    unreachable; without this they would sit in history as IN_PROGRESS forever.
    """
    async with AsyncSessionFactory() as db:
        return await LessonBattleRepository(db).abandon_orphaned()


async def sweep_battles(engine: BattleEngine | None = None) -> int:
    """Close any live battle that has outlived every plausible fight.

    Rides the sweep duo housekeeping already runs every minute rather than
    getting a scheduler of its own.
    """
    active = engine or default_engine
    stale = [
        battle
        for battle in active.registry.all_battles()
        if active.registry.age_seconds(battle) >= BATTLE_TTL_SECONDS
    ]
    for battle in stale:
        logger.info("Closing stale lesson battle %s", battle.battle_id)
        await active.close_stale_battle(battle)
    return len(stale)
