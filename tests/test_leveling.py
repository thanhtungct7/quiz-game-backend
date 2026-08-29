from app.services.game.leveling import (
    apply_exp,
    exp_for_level,
    exp_to_next_level,
    level_for_exp,
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
