"""The battle clock, and every constant the realtime loop is timed by.

Two scales meet here and must not be confused. `seconds()` is a monotonic
reading used *inside* the engine -- to expire effects, to know when the monster
may swing -- and never leaves the process. `server_ms()` is the same instant
published to clients as an absolute millisecond stamp, measured from one origin
shared by every battle in the process, so a client can compare `cast_ends_at`
against the `server_time_ms` it read from a `pong`.

Publishing deadlines rather than progress bars is what lets the snapshot rate
sit at 10 Hz while the client draws at 60: the client is told *when* the cast
lands, and fills the bar itself.
"""

import time

# The simulation advances faster than it is broadcast. Answering is resolved on
# the tick it arrives, so a player never waits for a snapshot to see their hit.
TICK_HZ = 15
TICK_SECONDS = 1.0 / TICK_HZ
SNAPSHOT_HZ = 10
SNAPSHOT_SECONDS = 1.0 / SNAPSHOT_HZ

# The window a correct answer is scored against. It replaces the round deadline
# lock-step used to hand `combat.resolve_blow`, and is passed to that same
# `time_limit_seconds` parameter, so the damage curve, the QUICK cutoff and the
# HEAVY ratio all keep working with no change to the combat maths.
SPEED_REFERENCE_SECONDS = 8

# Skills are authored in rounds (`skills.duration_rounds`) because duo still
# runs in rounds. A realtime battle has no round to count, so a duration is read
# as this many seconds per authored round. Deliberately not a migration: the
# column still means what it says for duo.
SECONDS_PER_ROUND = 6.0

# Being wrong costs tempo, not health -- the monster's own clock is the only
# thing that takes health off the player now. The pause is long enough to read
# the explanation and short enough to hurt.
WRONG_LOCKOUT_MS = 1500
# A correct answer still gets a beat before the next question, so a hit reads as
# a hit rather than as the screen changing under the player's thumb.
CORRECT_LOCKOUT_MS = 600

# A skill may be recast once its own effect has run out, and never sooner
# than this -- mana alone is not a fast enough gate when questions arrive
# back to back.
SKILL_MIN_COOLDOWN_SECONDS = 4.0

# A battle nobody is playing any more, swept by housekeeping.
BATTLE_TTL_SECONDS = 3600

_ORIGIN = time.monotonic()


def seconds() -> float:
    """Monotonic seconds, for comparing instants inside the engine."""
    return time.monotonic()


def server_ms() -> int:
    """The current instant as the absolute millisecond stamp clients are sent."""
    return int((time.monotonic() - _ORIGIN) * 1000)


def to_server_ms(at: float) -> int:
    """A monotonic reading from `seconds()`, on the published scale."""
    return int((at - _ORIGIN) * 1000)
