"""Energy: the budget that limits how many matches a day can hold.

Pure functions with no I/O. `now` is always a parameter and never read from the
clock in here, so every rule below is testable without waiting for time to pass.

Regeneration is lazy, like Duolingo's hearts: nothing runs in the background,
the stored value is simply interpreted against how long it has been sitting.
`updated_at` advances only by whole regeneration intervals, so the leftover
fraction of an interval is carried rather than thrown away on every read.
"""

from datetime import datetime, timedelta

from app.core.exceptions import NotEnoughEnergyError

MAX_ENERGY = 5
REGEN_MINUTES = 30
REGEN_INTERVAL = timedelta(minutes=REGEN_MINUTES)
MATCH_COST = 1
# Finishing a lesson refills faster than waiting does. That is the whole point:
# the way out of an empty bar is to go and study.
REVIEW_REFILL = 2


def regenerate(stored: int, updated_at: datetime, now: datetime) -> tuple[int, datetime]:
    """The energy that has accrued by `now`, and the timestamp to store with it.

    A full bar is not accruing anything, so its clock is simply moved to now.
    """
    if stored >= MAX_ENERGY:
        return MAX_ENERGY, now

    elapsed = now - updated_at
    if elapsed < timedelta(0):
        # Clock skew, or a timestamp written in the future. Never punish for it.
        return stored, updated_at

    ticks = int(elapsed // REGEN_INTERVAL)
    if ticks <= 0:
        return stored, updated_at

    gained = min(ticks, MAX_ENERGY - stored)
    restored = stored + gained
    if restored >= MAX_ENERGY:
        return MAX_ENERGY, now
    # Advance by whole intervals only: the remainder keeps counting toward the
    # next point instead of being reset by this read.
    return restored, updated_at + REGEN_INTERVAL * gained


def current_energy(stored: int, updated_at: datetime, now: datetime) -> int:
    return regenerate(stored, updated_at, now)[0]


def next_regen_at(stored: int, updated_at: datetime, now: datetime) -> datetime | None:
    """When the next point arrives, or None when the bar is already full."""
    energy, stamp = regenerate(stored, updated_at, now)
    if energy >= MAX_ENERGY:
        return None
    return stamp + REGEN_INTERVAL


def spend(
    stored: int, updated_at: datetime, now: datetime, amount: int = MATCH_COST
) -> tuple[int, datetime]:
    """Take energy, raising when there is not enough.

    Leaving a full bar starts the regeneration clock from this moment, so the
    first point back is a full interval away rather than arriving instantly.
    """
    energy, stamp = regenerate(stored, updated_at, now)
    if energy < amount:
        raise NotEnoughEnergyError(f"Needs {amount} energy, has {energy}")
    was_full = energy >= MAX_ENERGY
    return energy - amount, now if was_full else stamp


def refill(
    stored: int, updated_at: datetime, now: datetime, amount: int
) -> tuple[int, datetime]:
    """Give energy back, never past the cap."""
    energy, stamp = regenerate(stored, updated_at, now)
    restored = min(MAX_ENERGY, energy + max(0, amount))
    return restored, now if restored >= MAX_ENERGY else stamp
