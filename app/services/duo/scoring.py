"""Round scoring and end-of-match tiebreaking for duo matches.

Pure functions with no I/O. Elapsed time is always measured server-side by the
caller, never taken from the client. This is the whole PvP economy: a duo
match carries no equipment or class advantage (see
`loadout_builder.LoadoutBuilder.build`'s `pvp` flag), so the only thing that
can decide a match is what is scored here -- correctness, speed and combo.
"""

from dataclasses import dataclass
from enum import StrEnum

from app.services.game.combat import MAX_HP

MAX_POINTS = 1000
BASE_SHARE = 0.5

# The combo bonus on top of MAX_POINTS. Tiered rather than linear so it reads
# the same way the old damage combo did: nothing until a real streak forms,
# then a jump worth chasing. Mirrors `combat.COMBO_TIER_1`/`COMBO_TIER_2`
# so a Duo player's combo means the same length in both the score they earn
# and the Combo Shield lifeline that protects it.
COMBO_BONUS_TIER_1 = 3
COMBO_BONUS_TIER_1_POINTS = 50
COMBO_BONUS_TIER_2 = 5
COMBO_BONUS_TIER_2_POINTS = 120


def combo_bonus_points(combo_count: int) -> int:
    """Extra points a correct answer earns for the streak it extends."""
    if combo_count >= COMBO_BONUS_TIER_2:
        return COMBO_BONUS_TIER_2_POINTS
    if combo_count >= COMBO_BONUS_TIER_1:
        return COMBO_BONUS_TIER_1_POINTS
    return 0


class MatchOutcome(StrEnum):
    WIN = "WIN"
    LOSE = "LOSE"
    DRAW = "DRAW"


@dataclass(frozen=True)
class PlayerTotals:
    """What a tiebreak decision is made on, plus what is only ever reported."""

    score: int
    correct_count: int
    total_elapsed_ms: int
    # Carried for the match record and the result screen's health bar only --
    # `decide_outcome` never reads it. A Knowledge Arena match is not decided
    # by who is still standing, only by who knew more and knew it faster.
    hp_left: int = MAX_HP
    # True for a player who got every question in their deck right. Only one
    # of the two can hold it: the match stops the moment either does.
    deck_cleared: bool = False


def award_points(
    is_correct: bool, elapsed_ms: int, time_limit_seconds: int, combo_count: int = 0
) -> int:
    """Points for one answer: 0 when wrong, BASE..MAX plus a combo bonus when right.

    A correct answer is always worth at least half of MAX_POINTS; the other
    half is a speed bonus that decays linearly to zero at the deadline.
    `combo_count` is the streak *including* this answer, same convention as
    `combat.resolve_blow`, so the bonus lands on the answer that reached it.
    """
    if not is_correct:
        return 0

    limit_ms = time_limit_seconds * 1000
    if limit_ms <= 0:
        base = MAX_POINTS
    else:
        remaining = max(0, limit_ms - max(0, elapsed_ms))
        speed_share = (1.0 - BASE_SHARE) * (remaining / limit_ms)
        base = round(MAX_POINTS * (BASE_SHARE + speed_share))
    return base + combo_bonus_points(combo_count)


def decide_outcome(one: PlayerTotals, two: PlayerTotals) -> MatchOutcome:
    """Outcome from player one's point of view.

    Clearing the deck decides first: answering every question correctly is
    the thing the match is actually about, and a player who does it has
    finished the race before the other.

    Everything below that is the Knowledge Arena's whole scoreboard --
    correctness and speed (`score`), then the raw number of correct answers,
    then the lower cumulative answer time -- and nothing else. Health is
    deliberately not read here: a duo match carries no HP-based win condition
    any more, only a knockout end-reason for the fight to stop on, so two
    players are compared purely on what they knew and how fast they knew it.
    Equal on all four is a genuine draw.
    """
    if one.deck_cleared != two.deck_cleared:
        return MatchOutcome.WIN if one.deck_cleared else MatchOutcome.LOSE
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
