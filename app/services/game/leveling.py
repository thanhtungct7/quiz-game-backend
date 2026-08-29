"""Experience points and levels.

Pure functions with no I/O. `total_exp` is the source of truth everywhere; a
stored `level` is only ever a cache recomputed from it, so the two can never
drift apart in a way that matters.

The curve is triangular: each level costs 100 more experience than the one
before it, which puts level 2 at 100, level 5 at 1000 and level 20 at 19000.
"""

import math

EXP_PER_LEVEL_STEP = 100


def exp_for_level(level: int) -> int:
    """Total experience needed to have reached `level`.

    Level 1 is the floor and costs nothing.
    """
    if level <= 1:
        return 0
    return EXP_PER_LEVEL_STEP * (level - 1) * level // 2


def level_for_exp(total_exp: int) -> int:
    """The highest level `total_exp` has paid for.

    Solved directly instead of looped so it stays constant-time no matter how
    much experience a long-lived account accumulates. Uses `isqrt` rather than
    float `sqrt` because a float rounding error right on a level boundary would
    hand out — or take away — a level.
    """
    if total_exp <= 0:
        return 1
    half_step = EXP_PER_LEVEL_STEP // 2
    discriminant = half_step * half_step + 2 * EXP_PER_LEVEL_STEP * total_exp
    return (half_step + math.isqrt(discriminant)) // EXP_PER_LEVEL_STEP


def exp_to_next_level(total_exp: int) -> int:
    """How much more experience the next level needs."""
    return exp_for_level(level_for_exp(total_exp) + 1) - max(0, total_exp)


def apply_exp(total_exp: int, delta: int, *, floor_at_current_level: bool) -> int:
    """Add (or subtract) experience and return the new total.

    With `floor_at_current_level` the result never falls below the threshold of
    the level already reached. Losing a level would re-lock skills that were
    already unlocked and paid for, so a forfeit penalty is allowed to eat into
    progress toward the *next* level and no further.
    """
    updated = total_exp + delta
    if floor_at_current_level:
        floor = exp_for_level(level_for_exp(total_exp))
        updated = max(updated, floor)
    return max(0, updated)
