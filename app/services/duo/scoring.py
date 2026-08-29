"""Round scoring and end-of-match tiebreaking for duo matches.

Pure functions with no I/O. Elapsed time is always measured server-side by the
caller, never taken from the client.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.services.duo.combat import MAX_HP

MAX_POINTS = 1000
BASE_SHARE = 0.5


class MatchOutcome(StrEnum):
    WIN = "WIN"
    LOSE = "LOSE"
    DRAW = "DRAW"


@dataclass(frozen=True)
class PlayerTotals:
    """What a tiebreak decision is made on."""

    score: int
    correct_count: int
    total_elapsed_ms: int
    # Defaults to full health so a match played without combat decides on
    # score exactly as it always did.
    hp_left: int = MAX_HP


def award_points(is_correct: bool, elapsed_ms: int, time_limit_seconds: int) -> int:
    """Points for one answer: 0 when wrong, BASE..MAX when right.

    A correct answer is always worth at least half of MAX_POINTS; the other
    half is a speed bonus that decays linearly to zero at the deadline.
    """
    if not is_correct:
        return 0

    limit_ms = time_limit_seconds * 1000
    if limit_ms <= 0:
        return MAX_POINTS

    remaining = max(0, limit_ms - max(0, elapsed_ms))
    speed_share = (1.0 - BASE_SHARE) * (remaining / limit_ms)
    return round(MAX_POINTS * (BASE_SHARE + speed_share))


def decide_outcome(one: PlayerTotals, two: PlayerTotals) -> MatchOutcome:
    """Outcome from player one's point of view.

    Health decides first: whoever has more of it left has won the fight, and a
    player on zero has lost outright. Points still accumulate exactly as they
    did and still decide everything below that -- they just no longer come
    first. Two players who knocked each other out in the same round are level
    on health and fall through to the point tiebreaks like anyone else.

    Remaining tiebreaks in order: total score, then number of correct answers,
    then the lower cumulative answer time. Equal on all four is a genuine draw.
    """
    if one.hp_left != two.hp_left:
        return MatchOutcome.WIN if one.hp_left > two.hp_left else MatchOutcome.LOSE
    if one.score != two.score:
        return MatchOutcome.WIN if one.score > two.score else MatchOutcome.LOSE
    if one.correct_count != two.correct_count:
        return MatchOutcome.WIN if one.correct_count > two.correct_count else MatchOutcome.LOSE
    if one.total_elapsed_ms != two.total_elapsed_ms:
        return (
            MatchOutcome.WIN
            if one.total_elapsed_ms < two.total_elapsed_ms
            else MatchOutcome.LOSE
        )
    return MatchOutcome.DRAW


def invert(outcome: MatchOutcome) -> MatchOutcome:
    """The same result seen from the opponent's side."""
    if outcome is MatchOutcome.WIN:
        return MatchOutcome.LOSE
    if outcome is MatchOutcome.LOSE:
        return MatchOutcome.WIN
    return MatchOutcome.DRAW
