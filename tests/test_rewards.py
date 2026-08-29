from app.models.duo.duo_match import DuoMatchEndReason
from app.services.duo.scoring import MatchOutcome
from app.services.game.rewards import RewardInput, compute_reward


def _input(
    outcome: MatchOutcome,
    *,
    end_reason: DuoMatchEndReason = DuoMatchEndReason.COMPLETED,
    forfeited: bool = False,
    correct_count: int = 0,
    knockout: bool = False,
) -> RewardInput:
    return RewardInput(
        outcome=outcome,
        end_reason=end_reason,
        forfeited=forfeited,
        correct_count=correct_count,
        knockout=knockout,
    )


def test_a_win_scales_with_correct_answers() -> None:
    reward = compute_reward(_input(MatchOutcome.WIN, correct_count=8))
    assert reward.exp_delta == 50 + 5 * 8
    assert reward.gold_delta == 25 + 2 * 8


def test_a_knockout_win_pays_a_bonus_on_top() -> None:
    plain = compute_reward(_input(MatchOutcome.WIN, correct_count=8))
    knockout = compute_reward(_input(MatchOutcome.WIN, correct_count=8, knockout=True))
    assert knockout.exp_delta == plain.exp_delta + 25
    # The bonus is experience only; gold is unchanged.
    assert knockout.gold_delta == plain.gold_delta


def test_winning_because_the_opponent_left_pays_a_flat_amount() -> None:
    for end_reason in (
        DuoMatchEndReason.OPPONENT_LEFT,
        DuoMatchEndReason.OPPONENT_TIMEOUT,
    ):
        reward = compute_reward(
            _input(MatchOutcome.WIN, end_reason=end_reason, correct_count=8)
        )
        # Flat: the match never finished, so the per-answer component does not apply.
        assert reward.exp_delta == 40
        assert reward.gold_delta == 20


def test_a_draw_pays_more_than_a_loss() -> None:
    draw = compute_reward(_input(MatchOutcome.DRAW, correct_count=5))
    loss = compute_reward(_input(MatchOutcome.LOSE, correct_count=5))
    assert (draw.exp_delta, draw.gold_delta) == (25, 12)
    assert (loss.exp_delta, loss.gold_delta) == (15, 8)
    assert draw.exp_delta > loss.exp_delta


def test_losing_a_played_match_still_pays_something() -> None:
    reward = compute_reward(_input(MatchOutcome.LOSE, correct_count=3))
    assert reward.exp_delta > 0
    assert reward.gold_delta > 0


def test_forfeiting_is_the_only_thing_that_costs_experience() -> None:
    reward = compute_reward(
        _input(
            MatchOutcome.LOSE,
            end_reason=DuoMatchEndReason.OPPONENT_LEFT,
            forfeited=True,
            correct_count=4,
        )
    )
    assert reward.exp_delta == -30
    assert reward.gold_delta == 0


def test_forfeiting_ignores_answers_already_given() -> None:
    few = compute_reward(_input(MatchOutcome.LOSE, forfeited=True, correct_count=0))
    many = compute_reward(_input(MatchOutcome.LOSE, forfeited=True, correct_count=10))
    assert few == many


def test_no_outcome_other_than_a_forfeit_ever_loses_experience() -> None:
    for outcome in MatchOutcome:
        for end_reason in DuoMatchEndReason:
            reward = compute_reward(
                _input(outcome, end_reason=end_reason, correct_count=2)
            )
            assert reward.exp_delta > 0
            assert reward.gold_delta > 0
