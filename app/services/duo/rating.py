"""Elo rating maths for 1v1 duo matches.

Pure functions with no I/O so they can be unit tested without a database.
"""

K_FACTOR = 32
RATING_FLOOR = 100

WIN = 1.0
DRAW = 0.5
LOSS = 0.0


def expected_score(rating_a: int, rating_b: int) -> float:
    """Probability that A beats B under the Elo model."""
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400))


def apply_result(rating_a: int, rating_b: int, score_a: float) -> tuple[int, int]:
    """Return both players' new ratings after a match.

    `score_a` is A's result: WIN, DRAW or LOSS. B's score is its complement,
    so the pair is zero-sum before the floor is applied.
    """
    expected_a = expected_score(rating_a, rating_b)
    delta = round(K_FACTOR * (score_a - expected_a))
    new_a = max(RATING_FLOOR, rating_a + delta)
    new_b = max(RATING_FLOOR, rating_b - delta)
    return new_a, new_b


def streak_after(current_streak: int, score: float) -> int:
    """Consecutive wins, reset to 0 by any draw or loss."""
    return current_streak + 1 if score == WIN else 0
