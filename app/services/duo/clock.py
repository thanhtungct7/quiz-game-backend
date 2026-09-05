"""The duo match clock, and every constant the realtime loop is timed by.

Two scales meet here and must not be confused. `seconds()` is a monotonic
reading used *inside* the engine -- to expire effects, to know when a player's
lockout is up -- and never leaves the process. `server_ms()` is the same instant
published to clients as an absolute millisecond stamp, measured from one origin
shared by every match in the process, so a client can compare `deadline_at`
against the `server_time_ms` it read from a `pong`.

Deliberately a sibling of `app/services/pve/clock.py` rather than an import of
it. The two engines are timed differently -- a duo match has an end, a lesson
battle does not -- and PvE already keeps its own copy of the event enums for
exactly this reason: neither feature should be able to break the other by
retuning its own pacing.
"""

import time

from app.services.duo.state import MatchSettings

# The simulation advances faster than it is broadcast. An answer is resolved on
# the tick it arrives, so a player never waits for a snapshot to see their hit.
TICK_HZ = 15
TICK_SECONDS = 1.0 / TICK_HZ
SNAPSHOT_HZ = 10
SNAPSHOT_SECONDS = 1.0 / SNAPSHOT_HZ

# The beat between "match found" and the first question, so both players see
# who they are up against before either can answer.
COUNTDOWN_SECONDS = 3.0

# Being wrong costs tempo, not health. The opponent is still answering through
# this pause, which is what makes it hurt.
WRONG_LOCKOUT_MS = 1500
# A correct answer still gets a beat before the next question, so a hit reads
# as a hit rather than as the screen changing under the player's thumb.
CORRECT_LOCKOUT_MS = 600

# Skills are authored in rounds (`skills.duration_rounds`) because that is what
# the column meant when there were rounds. A realtime match has none, so a
# duration is read as this many seconds per authored round.
SECONDS_PER_ROUND = 6.0

# A skill may be recast once its own effect has run out, and never sooner than
# this -- mana alone is not a fast enough gate when questions arrive back to
# back.
SKILL_MIN_COOLDOWN_SECONDS = 4.0

# What a five-answer combo now buys. The old stun skipped the opponent's next
# round; with no rounds left to skip, it takes their seconds instead.
STUN_SECONDS = 4.0

# How much longer than the nominal reading time a match may run before it is
# decided on health. Generous: the cap is a backstop against two players who
# stall each other out, not a deadline anyone should feel.
TIME_CAP_FACTOR = 1.5

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


def match_time_cap(settings: MatchSettings) -> float:
    """How long a match may run before health decides it.

    Derived from the settings the players agreed to rather than a flat number,
    so a twenty-question match is not cut off at the same second as a
    three-question one.
    """
    return settings.question_count * settings.time_per_question * TIME_CAP_FACTOR
