from app.services.duo.rating import (
    DRAW,
    LOSS,
    RATING_FLOOR,
    WIN,
    apply_result,
    expected_score,
    streak_after,
)


def test_expected_score_is_even_for_equal_ratings() -> None:
    assert expected_score(1000, 1000) == 0.5


def test_expected_score_favours_the_higher_rating() -> None:
    assert expected_score(1400, 1000) > 0.9
    assert expected_score(1000, 1400) < 0.1


def test_equal_ratings_draw_leaves_both_unchanged() -> None:
    assert apply_result(1000, 1000, DRAW) == (1000, 1000)


def test_underdog_win_gains_more_than_favourite_win() -> None:
    underdog_gain = apply_result(1000, 1400, WIN)[0] - 1000
    favourite_gain = apply_result(1400, 1000, WIN)[0] - 1400
    assert underdog_gain > favourite_gain


def test_rating_exchange_is_zero_sum() -> None:
    new_a, new_b = apply_result(1200, 1050, WIN)
    assert (new_a - 1200) == -(new_b - 1050)


def test_rating_never_falls_below_the_floor() -> None:
    new_a, _ = apply_result(RATING_FLOOR, 2000, LOSS)
    assert new_a == RATING_FLOOR


def test_streak_grows_on_win_and_resets_otherwise() -> None:
    assert streak_after(3, WIN) == 4
    assert streak_after(3, DRAW) == 0
    assert streak_after(3, LOSS) == 0
