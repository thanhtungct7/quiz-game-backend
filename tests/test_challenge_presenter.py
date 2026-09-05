import random

from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.models.content.challenge_option import ChallengeOption
from app.services.content.challenge_presenter import to_public_challenge

WORDS = ["Tôi", "phải", "đi", "ngủ", "sớm", "hôm", "nay"]


def _order_challenge(challenge_id: str = "c-order") -> Challenge:
    return Challenge(
        id=challenge_id,
        lesson_id="lesson-1",
        type=ChallengeType.ORDER,
        question="Sắp xếp các từ",
        difficulty=ChallengeDifficulty.EASY,
        order_index=1,
        # As loaded from the database: sorted by order_index, which for this
        # type is the solution itself.
        options=[
            ChallengeOption(id=f"o{position}", text=word, correct=True, order_index=position)
            for position, word in enumerate(WORDS, start=1)
        ],
    )


def _select_challenge() -> Challenge:
    return Challenge(
        id="c-select",
        lesson_id="lesson-1",
        type=ChallengeType.SELECT,
        question="Pick one",
        difficulty=ChallengeDifficulty.EASY,
        order_index=1,
        options=[
            ChallengeOption(id="s1", text="A", correct=True, order_index=1),
            ChallengeOption(id="s2", text="B", correct=False, order_index=2),
        ],
    )


def test_order_options_are_not_served_in_solution_order() -> None:
    public = to_public_challenge(_order_challenge())

    assert [option.text for option in public.options] != WORDS
    assert sorted(option.text for option in public.options) == sorted(WORDS)


def test_order_option_index_is_display_position_not_the_answer() -> None:
    """`order_index` is the answer key for this type, so the public view
    renumbers it -- otherwise shuffling the list would leak it anyway."""
    public = to_public_challenge(_order_challenge())

    assert [option.order_index for option in public.options] == list(range(1, len(WORDS) + 1))
    by_id = {option.id: option.order_index for option in public.options}
    assert by_id["o1"] != 1 or by_id["o2"] != 2


def test_order_layout_is_stable_across_fetches() -> None:
    """A re-fetch mid-lesson must not reshuffle the word bank under the
    learner's thumb."""
    first = to_public_challenge(_order_challenge())
    second = to_public_challenge(_order_challenge())

    assert [option.id for option in first.options] == [option.id for option in second.options]


def test_single_choice_options_keep_stored_order_without_an_rng() -> None:
    public = to_public_challenge(_select_challenge())

    assert [option.id for option in public.options] == ["s1", "s2"]
    assert [option.order_index for option in public.options] == [1, 2]


def test_an_rng_shuffles_every_type() -> None:
    rng = random.Random(1)  # noqa: S311 -- shuffling quiz options, not security-sensitive
    public = to_public_challenge(_select_challenge(), rng)

    assert sorted(option.id for option in public.options) == ["s1", "s2"]
