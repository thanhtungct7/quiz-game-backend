"""Process-local registry of live lesson battles.

Everything mutable is behind a single `asyncio.Lock`, so a client that sends
two `battle.start` frames in the same event-loop tick cannot open two battles.

This module is the only place that knows the state is in-memory. As with duo,
that means the service runs on one worker; swapping this for Redis later means
reimplementing this class alone.
"""

import asyncio
import time

from app.services.pve.state import LiveBattle


class BattleRegistry:
    def __init__(self) -> None:
        self._battles: dict[str, LiveBattle] = {}
        self._user_battle: dict[str, str] = {}
        # Callers hold this around any read-then-write sequence.
        self.lock = asyncio.Lock()

    def register(self, battle: LiveBattle) -> None:
        self._battles[battle.battle_id] = battle
        self._user_battle[battle.user_id] = battle.battle_id

    def unregister(self, battle_id: str) -> LiveBattle | None:
        battle = self._battles.pop(battle_id, None)
        if battle is None:
            return None
        if self._user_battle.get(battle.user_id) == battle_id:
            del self._user_battle[battle.user_id]
        return battle

    def get(self, battle_id: str) -> LiveBattle | None:
        return self._battles.get(battle_id)

    def battle_of_user(self, user_id: str) -> LiveBattle | None:
        battle_id = self._user_battle.get(user_id)
        return self._battles.get(battle_id) if battle_id else None

    def is_busy(self, user_id: str) -> bool:
        return user_id in self._user_battle

    def all_battles(self) -> list[LiveBattle]:
        return list(self._battles.values())

    def age_seconds(self, battle: LiveBattle) -> int:
        return int(time.monotonic() - battle.created_at)


registry = BattleRegistry()
