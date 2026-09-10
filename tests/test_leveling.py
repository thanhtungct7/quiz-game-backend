from app.services.game.cefr import LEVEL_CAPS
from app.services.game.leveling import (
    apply_exp,
    effective_level,
    exp_for_level,
    exp_to_next_level,
    level_for_exp,
    next_level_cap,
    pending_benchmark_level,
)


def test_level_thresholds_follow_the_documented_curve() -> None:
    assert exp_for_level(1) == 0
    assert exp_for_level(2) == 100
    assert exp_for_level(3) == 300
    assert exp_for_level(4) == 600
    assert exp_for_level(5) == 1000
    assert exp_for_level(10) == 4500
    assert exp_for_level(20) == 19000


def test_level_one_is_the_floor() -> None:
    assert exp_for_level(0) == 0
    assert exp_for_level(-5) == 0
    assert level_for_exp(0) == 1
    assert level_for_exp(-50) == 1


def test_level_for_exp_is_exact_on_both_sides_of_every_boundary() -> None:
    for level in range(1, 60):
        threshold = exp_for_level(level)
        assert level_for_exp(threshold) == level
        if threshold > 0:
            assert level_for_exp(threshold - 1) == level - 1


def test_level_for_exp_inverts_exp_for_level_across_the_range() -> None:
    for total_exp in range(0, 5000, 7):
        level = level_for_exp(total_exp)
        assert exp_for_level(level) <= total_exp < exp_for_level(level + 1)


def test_exp_to_next_level_counts_down_to_the_next_threshold() -> None:
    assert exp_to_next_level(0) == 100
    assert exp_to_next_level(99) == 1
    assert exp_to_next_level(100) == 200
    assert exp_to_next_level(299) == 1


def test_gaining_exp_adds_up() -> None:
    assert apply_exp(0, 50, floor_at_current_level=False) == 50
    assert apply_exp(250, 100, floor_at_current_level=True) == 350


def test_a_penalty_never_costs_a_level() -> None:
    # Level 3 starts at 300; a 30-point penalty eats into progress toward 4
    # but must not drop the player back to level 2.
    assert apply_exp(310, -30, floor_at_current_level=True) == 300
    assert level_for_exp(apply_exp(310, -30, floor_at_current_level=True)) == 3


def test_a_penalty_at_a_level_threshold_changes_nothing() -> None:
    assert apply_exp(300, -30, floor_at_current_level=True) == 300


def test_a_penalty_without_the_floor_can_drop_a_level() -> None:
    assert apply_exp(310, -30, floor_at_current_level=False) == 280
    assert level_for_exp(280) == 2


def test_exp_never_goes_negative() -> None:
    assert apply_exp(10, -999, floor_at_current_level=False) == 0
    assert apply_exp(0, -999, floor_at_current_level=True) == 0


# --- the Benchmark Exam level caps -------------------------------------------


def test_next_level_cap_is_the_lowest_one_not_yet_cleared() -> None:
    assert next_level_cap(0) == LEVEL_CAPS[0]
    assert next_level_cap(LEVEL_CAPS[0]) == LEVEL_CAPS[1]
    assert next_level_cap(LEVEL_CAPS[-1]) is None


def test_next_level_cap_treats_a_missing_column_as_nothing_cleared() -> None:
    """An ORM row built in memory and never flushed reads a server-default
    column as None; a level read must not crash on that."""
    assert next_level_cap(None) == LEVEL_CAPS[0]


def test_clearing_past_the_last_cap_is_still_just_the_last_cap() -> None:
    assert next_level_cap(LEVEL_CAPS[-1] + 50) is None


def test_experience_alone_carries_a_player_up_to_the_cap() -> None:
    cap = LEVEL_CAPS[0]
    assert effective_level(exp_for_level(cap), benchmark_cleared_level=0) == cap


def test_experience_past_the_cap_does_not_raise_the_level() -> None:
    cap = LEVEL_CAPS[0]
    assert effective_level(exp_for_level(cap + 5), benchmark_cleared_level=0) == cap


def test_experience_past_the_cap_is_never_wasted() -> None:
    """Nothing is floored or discarded -- only the level stays held back.
    `level_for_exp` and `apply_exp` know nothing about caps at all; the raw
    curve keeps climbing underneath a level that has stopped moving."""
    cap = LEVEL_CAPS[0]
    total_exp = exp_for_level(cap + 5)
    assert level_for_exp(total_exp) == cap + 5
    grown = apply_exp(total_exp, 50, floor_at_current_level=True)
    assert grown == total_exp + 50
    assert level_for_exp(grown) >= level_for_exp(total_exp)


def test_clearing_a_cap_releases_the_level_up_to_the_next_one() -> None:
    cap = LEVEL_CAPS[0]
    total_exp = exp_for_level(cap + 5)
    assert effective_level(total_exp, benchmark_cleared_level=cap) == cap + 5


def test_clearing_every_cap_reads_the_raw_curve() -> None:
    total_exp = exp_for_level(LEVEL_CAPS[-1] + 10)
    assert effective_level(total_exp, benchmark_cleared_level=LEVEL_CAPS[-1]) == (
        LEVEL_CAPS[-1] + 10
    )


def test_below_every_cap_the_level_is_never_touched() -> None:
    assert effective_level(exp_for_level(3), benchmark_cleared_level=0) == 3


def test_a_missing_benchmark_column_still_caps_at_the_first_level_cap() -> None:
    cap = LEVEL_CAPS[0]
    assert effective_level(exp_for_level(cap + 5), benchmark_cleared_level=None) == cap


def test_nothing_is_pending_below_the_cap() -> None:
    cap = LEVEL_CAPS[0]
    assert pending_benchmark_level(exp_for_level(cap - 1), benchmark_cleared_level=0) is None


def test_a_cap_is_pending_once_experience_reaches_it() -> None:
    cap = LEVEL_CAPS[0]
    assert pending_benchmark_level(exp_for_level(cap), benchmark_cleared_level=0) == cap


def test_nothing_is_pending_once_the_cap_holding_the_level_back_is_cleared() -> None:
    cap = LEVEL_CAPS[0]
    total_exp = exp_for_level(cap + 5)
    assert pending_benchmark_level(total_exp, benchmark_cleared_level=cap) is None


def test_nothing_is_pending_past_the_last_cap() -> None:
    total_exp = exp_for_level(LEVEL_CAPS[-1] + 10)
    assert pending_benchmark_level(total_exp, benchmark_cleared_level=LEVEL_CAPS[-1]) is None
