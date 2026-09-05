from app.services.duo.scoring import (
    MAX_POINTS,
    MatchOutcome,
    PlayerTotals,
    award_points,
    decide_outcome,
    invert,
)


def test_wrong_answer_scores_nothing() -> None:
    assert award_points(is_correct=False, elapsed_ms=0, time_limit_seconds=15) == 0


def test_instant_correct_answer_scores_the_maximum() -> None:
    assert award_points(is_correct=True, elapsed_ms=0, time_limit_seconds=15) == MAX_POINTS


def test_answer_at_the_deadline_still_scores_the_base_half() -> None:
    assert award_points(is_correct=True, elapsed_ms=15_000, time_limit_seconds=15) == 500


def test_late_answer_is_clamped_to_the_base_half() -> None:
    assert award_points(is_correct=True, elapsed_ms=99_000, time_limit_seconds=15) == 500


def test_points_decay_with_time_spent() -> None:
    fast = award_points(is_correct=True, elapsed_ms=3_000, time_limit_seconds=15)
    slow = award_points(is_correct=True, elapsed_ms=12_000, time_limit_seconds=15)
    assert MAX_POINTS > fast > slow > 500


def test_higher_score_wins() -> None:
    one = PlayerTotals(score=2000, correct_count=2, total_elapsed_ms=9_000)
    two = PlayerTotals(score=1500, correct_count=3, total_elapsed_ms=1_000)
    assert decide_outcome(one, two) is MatchOutcome.WIN


def test_tie_on_score_is_broken_by_correct_count() -> None:
    one = PlayerTotals(score=1500, correct_count=2, total_elapsed_ms=1_000)
    two = PlayerTotals(score=1500, correct_count=3, total_elapsed_ms=9_000)
    assert decide_outcome(one, two) is MatchOutcome.LOSE


def test_tie_on_score_and_correct_count_is_broken_by_speed() -> None:
    one = PlayerTotals(score=1500, correct_count=3, total_elapsed_ms=4_000)
    two = PlayerTotals(score=1500, correct_count=3, total_elapsed_ms=9_000)
    assert decide_outcome(one, two) is MatchOutcome.WIN


def test_identical_totals_are_a_draw() -> None:
    totals = PlayerTotals(score=1500, correct_count=3, total_elapsed_ms=4_000)
    assert decide_outcome(totals, totals) is MatchOutcome.DRAW


def test_clearing_the_deck_beats_everything() -> None:
    """Answering every question right is the race the match is about; being
    healthier only means you lost the race in better shape."""
    one = PlayerTotals(
        score=0, correct_count=3, total_elapsed_ms=99_000, hp_left=1, deck_cleared=True
    )
    two = PlayerTotals(
        score=9000, correct_count=9, total_elapsed_ms=1_000, hp_left=100
    )

    assert decide_outcome(one, two) is MatchOutcome.WIN
    assert decide_outcome(two, one) is MatchOutcome.LOSE


def test_neither_clearing_the_deck_decides_on_health_as_before() -> None:
    one = PlayerTotals(score=0, correct_count=0, total_elapsed_ms=0, hp_left=40)
    two = PlayerTotals(score=9000, correct_count=9, total_elapsed_ms=0, hp_left=10)

    assert decide_outcome(one, two) is MatchOutcome.WIN


def test_health_beats_score() -> None:
    # Losing the fight while winning on points is still losing.
    one = PlayerTotals(score=3000, correct_count=3, total_elapsed_ms=1_000, hp_left=0)
    two = PlayerTotals(score=500, correct_count=1, total_elapsed_ms=9_000, hp_left=12)
    assert decide_outcome(one, two) is MatchOutcome.LOSE
    assert decide_outcome(two, one) is MatchOutcome.WIN


def test_a_knockout_is_decided_by_health_alone() -> None:
    winner = PlayerTotals(score=0, correct_count=0, total_elapsed_ms=0, hp_left=1)
    loser = PlayerTotals(score=0, correct_count=0, total_elapsed_ms=0, hp_left=0)
    assert decide_outcome(winner, loser) is MatchOutcome.WIN


def test_a_double_knockout_falls_through_to_the_point_tiebreaks() -> None:
    one = PlayerTotals(score=2000, correct_count=2, total_elapsed_ms=4_000, hp_left=0)
    two = PlayerTotals(score=1500, correct_count=2, total_elapsed_ms=4_000, hp_left=0)
    assert decide_outcome(one, two) is MatchOutcome.WIN


def test_equal_health_decides_on_points_exactly_as_before() -> None:
    one = PlayerTotals(score=2000, correct_count=2, total_elapsed_ms=9_000, hp_left=40)
    two = PlayerTotals(score=1500, correct_count=3, total_elapsed_ms=1_000, hp_left=40)
    assert decide_outcome(one, two) is MatchOutcome.WIN


def test_full_health_is_the_default_so_score_still_decides() -> None:
    one = PlayerTotals(score=2000, correct_count=2, total_elapsed_ms=9_000)
    two = PlayerTotals(score=1500, correct_count=3, total_elapsed_ms=1_000)
    assert decide_outcome(one, two) is MatchOutcome.WIN


def test_identical_totals_including_health_are_a_draw() -> None:
    totals = PlayerTotals(score=1500, correct_count=3, total_elapsed_ms=4_000, hp_left=55)
    assert decide_outcome(totals, totals) is MatchOutcome.DRAW


def test_invert_swaps_win_and_lose_but_keeps_draw() -> None:
    assert invert(MatchOutcome.WIN) is MatchOutcome.LOSE
    assert invert(MatchOutcome.LOSE) is MatchOutcome.WIN
    assert invert(MatchOutcome.DRAW) is MatchOutcome.DRAW
