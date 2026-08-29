"""Charging and refunding energy around a match.

The rules live in `energy.py`; this is the thin layer that reads and writes
them. It never partially applies: every player is checked before any is
charged, so a rejected start leaves nothing to undo.
"""

from datetime import UTC, datetime

from app.repository.game.game_profile_repository import GameProfileRepository
from app.services.game.energy import MATCH_COST, current_energy, refill, spend


class EnergyService:
    def __init__(self, *, profiles: GameProfileRepository) -> None:
        self.profiles = profiles

    async def who_is_out_of_energy(self, user_ids: list[str]) -> list[str]:
        """The players who cannot afford a match right now."""
        now = datetime.now(UTC)
        short = []
        for user_id in user_ids:
            profile = await self.profiles.get_or_create(user_id)
            if current_energy(profile.energy, profile.energy_updated_at, now) < MATCH_COST:
                short.append(user_id)
        return short

    async def spend_for_match(self, user_ids: list[str]) -> None:
        now = datetime.now(UTC)
        for user_id in user_ids:
            profile = await self.profiles.get_or_create(user_id)
            remaining, stamp = spend(
                profile.energy, profile.energy_updated_at, now, MATCH_COST
            )
            await self.profiles.save(
                profile,
                {"energy": remaining, "energy_updated_at": stamp, "updated_at": now},
            )

    async def refund_match(self, user_ids: list[str]) -> None:
        """Hand back a match's worth of energy for a match that never ran."""
        now = datetime.now(UTC)
        for user_id in user_ids:
            profile = await self.profiles.get_by_user(user_id)
            if profile is None:
                continue
            restored, stamp = refill(
                profile.energy, profile.energy_updated_at, now, MATCH_COST
            )
            await self.profiles.save(
                profile,
                {"energy": restored, "energy_updated_at": stamp, "updated_at": now},
            )

    async def grant_for_lesson(self, user_id: str, amount: int) -> None:
        now = datetime.now(UTC)
        profile = await self.profiles.get_or_create(user_id)
        restored, stamp = refill(profile.energy, profile.energy_updated_at, now, amount)
        await self.profiles.save(
            profile,
            {"energy": restored, "energy_updated_at": stamp, "updated_at": now},
        )
