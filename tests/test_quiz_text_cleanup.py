"""scripts/quiz_text_cleanup.py: what it repairs in the quiz source text, and
what it must leave alone."""

from typing import Any

import pytest

from scripts.import_quiz_bank import load_json, transform_multiple_choice_4options
from scripts.quiz_text_cleanup import (
    clean_sentence,
    clean_text,
    rejoin_split_words,
    tidy_punctuation,
)


@pytest.mark.parametrize(
    ("broken", "fixed"),
    [
        ("hav e", "have"),
        ("Hav e", "Have"),
        ("readin g", "reading"),
        ("to readin g", "to reading"),
        ("the y", "they"),
        ("man y", "many"),
        ("isn't i t", "isn't it"),
        ("a lo t", "a lot"),
        ("was shared wit h", "was shared with"),
        ("the only transportatio n to Ellis Island", "the only transportation to Ellis Island"),
    ],
)
def test_a_split_off_last_letter_is_joined_back(broken: str, fixed: str) -> None:
    assert rejoin_split_words(broken) == fixed


@pytest.mark.parametrize(
    "text",
    [
        "You are a very professional ... .",
        "You may be wrong.",
        "She forgot her e-mail password.",
        "Mandy writes in her e.mail that",
        "No, it s Bill's.",
        "I was sure that I d be safe.",
        "have a d discussion with their family",
        "a b c d",
        "changing the letter u to o when it came before m",
        "Which would be t he best title",
        "if the n ew tickets cost less",
        "World War n",
        "Take vitamin C every day.",
    ],
)
def test_text_that_only_looks_split_is_left_alone(text: str) -> None:
    assert rejoin_split_words(text) == text


def test_the_space_before_a_full_stop_goes_but_an_ellipsis_stays() -> None:
    assert tidy_punctuation("They money is hers .") == "They money is hers."
    assert tidy_punctuation("Yes , they did .") == "Yes, they did."
    assert tidy_punctuation("Wait ... then go.") == "Wait ... then go."


def test_manual_fixes_come_before_the_automatic_pass() -> None:
    assert clean_sentence("mc4:1671", "They money is hers .") == "The money is hers."
    # Rejoined first, "hav e" would duplicate the option "have".
    assert clean_text("mc4:1535", "hav e") == "do"
    assert clean_text("mc4:9999", "hav e") == "have"


Item = tuple[dict[str, Any], list[dict[str, Any]]]


@pytest.fixture(scope="module")
def multiple_choice() -> dict[str, tuple[Item, Item]]:
    """source_ref -> (as the source has it, as the import writes it)."""
    rows = load_json("quiz_multiple_choice_4options.json")
    return {
        clean[0]["source_ref"]: (raw, clean)
        for raw, clean in zip(
            transform_multiple_choice_4options(rows, clean=False),
            transform_multiple_choice_4options(rows),
            strict=True,
        )
    }


def test_no_split_word_survives_the_multiple_choice_import(
    multiple_choice: dict[str, tuple[Item, Item]],
) -> None:
    leftovers = []
    for source_ref, (_raw, (fields, options)) in multiple_choice.items():
        texts = [fields["question"], fields["explanation"] or ""]
        texts += [option["text"] for option in options]
        leftovers += [(source_ref, text) for text in texts if rejoin_split_words(text) != text]
    assert leftovers == []


def _repeats_an_option(options: list[dict[str, Any]]) -> bool:
    return len({str(option["text"]).strip().lower() for option in options}) < len(options)


def test_cleaning_never_makes_two_options_the_same(
    multiple_choice: dict[str, tuple[Item, Item]],
) -> None:
    # The source already repeats an option in a few questions ("cost", "cost",
    # "costed"); that is its own fault. What must not happen is a rejoined
    # distractor turning into a copy of another option.
    made_equal = [
        source_ref
        for source_ref, ((_f, raw_options), (_g, clean_options)) in multiple_choice.items()
        if _repeats_an_option(clean_options) and not _repeats_an_option(raw_options)
    ]
    assert made_equal == []


def test_the_reported_questions_now_read_correctly(
    multiple_choice: dict[str, tuple[Item, Item]],
) -> None:
    _raw, (fields, options) = multiple_choice["mc4:817"]
    assert [option["text"] for option in options] == ["to read", "read", "reading"]

    _raw, (fields, _options) = multiple_choice["mc4:1671"]
    assert fields["question"] == "The money is ... ."
    assert fields["explanation"] == "Câu hoàn chỉnh: The money is hers."
