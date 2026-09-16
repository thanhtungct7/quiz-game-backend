"""scripts/quiz_option_repairs.py: that no imported question offers the same
option twice, and that repairing one never touches its answer."""

from typing import Any

import pytest

from scripts.import_quiz_bank import (
    load_json,
    transform_explanations_mcq,
    transform_multiple_choice_4options,
    transform_reading_comprehension,
)
from scripts.quiz_option_repairs import (
    ANSWER_KEY_FIXES,
    DROPPED_QUESTIONS,
    OPTION_REPAIRS,
    RepairedTheAnswerError,
    apply_answer_key,
    repair_options,
)


def _duplicated(options: list[dict[str, Any]]) -> set[str]:
    seen: set[str] = set()
    repeated: set[str] = set()
    for option in options:
        if option["text"] in seen:
            repeated.add(option["text"])
        seen.add(option["text"])
    return repeated


@pytest.fixture(scope="module")
def mc4_items() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    return list(transform_multiple_choice_4options(load_json("quiz_multiple_choice_4options.json")))


def test_no_multiple_choice_question_repeats_an_option(
    mc4_items: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> None:
    offenders = {
        fields["source_ref"]: sorted(_duplicated(options))
        for fields, options in mc4_items
        if _duplicated(options)
    }
    assert offenders == {}


def test_a_repair_replaces_the_duplicate_and_keeps_the_answer(
    mc4_items: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> None:
    """mc4:14 offered "cost" twice, one copy of it the answer."""
    fields, options = next(f_o for f_o in mc4_items if f_o[0]["source_ref"] == "mc4:14")
    assert [option["text"] for option in options] == ["costs", "cost", "costed"]
    assert [option["text"] for option in options if option["correct"]] == ["cost"]


def test_a_repair_aimed_at_the_correct_option_raises() -> None:
    options = [
        {"text": "cost", "correct": False},
        {"text": "cost", "correct": True},
    ]
    with pytest.raises(RepairedTheAnswerError):
        repair_options("mc4:14", [options[1], options[0]])


def test_a_question_with_no_repair_is_untouched() -> None:
    options = [{"text": "a", "correct": True}, {"text": "b", "correct": False}]
    assert repair_options("mc4:no-such-question", options) == options


def test_every_repaired_and_dropped_question_exists_in_the_source(
    mc4_items: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> None:
    """A stale key would silently repair nothing."""
    mc4_refs = {fields["source_ref"] for fields, _ in mc4_items}
    listed_mc4 = {ref for ref in OPTION_REPAIRS if ref.startswith("mc4:")}
    assert listed_mc4 <= mc4_refs
    assert not any(ref.startswith("mc4:") for ref in DROPPED_QUESTIONS)


def test_no_question_is_both_repaired_and_dropped() -> None:
    assert not (set(OPTION_REPAIRS) & DROPPED_QUESTIONS)


@pytest.mark.slow
@pytest.mark.parametrize("part", range(1, 6))
def test_no_reading_question_repeats_an_option(part: int) -> None:
    """The five reading files are 240 MB together, so each is read on its own."""
    rows = load_json(f"quiz_reading_comprehension_part{part}.json")
    offenders = {
        fields["source_ref"]: sorted(_duplicated(options))
        for fields, options, _passage in transform_reading_comprehension(rows)
        if _duplicated(options)
    }
    assert offenders == {}


@pytest.mark.slow
@pytest.mark.parametrize("part", range(1, 6))
def test_dropped_reading_questions_are_not_imported(part: int) -> None:
    rows = load_json(f"quiz_reading_comprehension_part{part}.json")
    imported = {fields["source_ref"] for fields, _options, _passage in
                transform_reading_comprehension(rows)}
    assert not (imported & DROPPED_QUESTIONS)


# --- questions the source left with no correct option ------------------------


@pytest.fixture(scope="module")
def mcq_items() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    explanations = load_json("quiz_with_explanations.json")
    return list(transform_explanations_mcq(explanations["multiple_choice_quizzes"]))


def test_every_imported_question_has_an_answer_to_grade_against(
    mc4_items: list[tuple[dict[str, Any], list[dict[str, Any]]]],
    mcq_items: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> None:
    """An option-less question is served like any other and marks every answer
    wrong, so none may reach the import."""
    unanswerable = [
        fields["source_ref"]
        for fields, options in [*mc4_items, *mcq_items]
        if not any(option["correct"] for option in options)
    ]
    assert unanswerable == []


def test_the_intended_answer_is_restored(
    mcq_items: list[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> None:
    """explanations.mcq:1200 carried "off" -- the answer's text, not its key."""
    _fields, options = next(f_o for f_o in mcq_items if f_o[0]["source_ref"] ==
                            "explanations.mcq:1200")
    assert [option["text"] for option in options if option["correct"]] == ["off"]


def test_an_answer_key_fix_steps_aside_when_the_source_marks_an_answer() -> None:
    options = [{"text": "out", "correct": True}, {"text": "off", "correct": False}]
    apply_answer_key("explanations.mcq:1200", options)
    assert [option["text"] for option in options if option["correct"]] == ["out"]


def test_no_question_is_both_answer_fixed_and_dropped() -> None:
    assert not (set(ANSWER_KEY_FIXES) & DROPPED_QUESTIONS)
