"""What a finished duo match pays out in experience and gold.

Pure functions with no I/O. The numbers live here rather than in
`app/core/config.py` on purpose: config is for deployment concerns, and game
balance is something that gets tuned by reading the table below next to the
rules it implements, not by reading an env file.

Elo is deliberately not part of this — `app/services/duo/rating.py` stays its
own layer and is unchanged.
"""

from dataclasses import dataclass

from app.models.duo.duo_match import DuoMatchEndReason
from app.services.duo.scoring import MatchOutcome

WIN_BASE_EXP = 50
WIN_EXP_PER_CORRECT = 5
KNOCKOUT_BONUS_EXP = 25
WIN_BY_FORFEIT_EXP = 40
DRAW_EXP = 25
LOSS_EXP = 15

# Only quitting is punished. Losing a match that was played to the end still
# pays a consolation, so that trying and failing always beats not playing.
FORFEIT_EXP = -30

WIN_BASE_GOLD = 25
WIN_GOLD_PER_CORRECT = 2
WIN_BY_FORFEIT_GOLD = 20
DRAW_GOLD = 12
LOSS_GOLD = 8
FORFEIT_GOLD = 0


@dataclass(frozen=True)
class RewardInput:
    """Everything a payout depends on, gathered by the caller."""

    outcome: MatchOutcome
    end_reason: DuoMatchEndReason
    forfeited: bool
    correct_count: int
    knockout: bool = False


@dataclass(frozen=True)
class Reward:
    exp_delta: int
    gold_delta: int


def _won_because_opponent_quit(data: RewardInput) -> bool:
    return data.end_reason in (
        DuoMatchEndReason.OPPONENT_LEFT,
        DuoMatchEndReason.OPPONENT_TIMEOUT,
    )


def compute_reward(data: RewardInput) -> Reward:
    """The payout for one player.

    `forfeited` is about *this* player walking out, which is the only case that
    costs experience. A player who wins because the other side walked out is a
    separate, smaller-than-normal win: they never got to finish the match, so
    there is no per-answer component to pay.
    """
    if data.forfeited:
        return Reward(exp_delta=FORFEIT_EXP, gold_delta=FORFEIT_GOLD)

    if data.outcome is MatchOutcome.DRAW:
        return Reward(exp_delta=DRAW_EXP, gold_delta=DRAW_GOLD)

    if data.outcome is MatchOutcome.LOSE:
        return Reward(exp_delta=LOSS_EXP, gold_delta=LOSS_GOLD)

    if _won_because_opponent_quit(data):
        return Reward(exp_delta=WIN_BY_FORFEIT_EXP, gold_delta=WIN_BY_FORFEIT_GOLD)

    exp = WIN_BASE_EXP + WIN_EXP_PER_CORRECT * data.correct_count
    if data.knockout:
        exp += KNOCKOUT_BONUS_EXP
    return Reward(
        exp_delta=exp,
        gold_delta=WIN_BASE_GOLD + WIN_GOLD_PER_CORRECT * data.correct_count,
    )
