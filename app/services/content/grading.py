"""Grading one submitted answer against a stored challenge.

Pure functions over a loaded `Challenge`, with no I/O and no record of the
attempt: `ProgressService` grades a study answer and then records it, while the
Benchmark Exam grades the same way but must not count as study. Keeping the
rules here is what stops the two from ever disagreeing about what is correct.
"""

from dataclasses import dataclass

from app.core.exceptions import ChallengeOptionNotFoundError, InvalidAnswerSubmissionError
from app.models.content.challenge import Challenge, ChallengeType


@dataclass(frozen=True)
class GradedAnswer:
    """One graded submission, in the shape `AnswerCheckResult` needs it."""

    correct: bool
    recorded_option_id: str | None
    submitted_option_ids: list[str]
    correct_option_ids: list[str]


def grade(
    challenge: Challenge,
    selected_option_id: str | None,
    selected_option_ids: list[str] | None,
) -> GradedAnswer:
    """Grade whichever answer shape the challenge type takes.

    Which of the two arguments carries the answer is decided by the challenge
    type, not by which one the caller happened to fill in: sending the wrong
    shape is a bad request, never a silently mis-graded answer.
    """
    if challenge.type is ChallengeType.ORDER:
        return grade_ordered(challenge, selected_option_ids)
    return grade_single_choice(challenge, selected_option_id)


def grade_single_choice(challenge: Challenge, selected_option_id: str | None) -> GradedAnswer:
    if selected_option_id is None:
        raise InvalidAnswerSubmissionError(
            f"{challenge.type.value} challenges are answered with selected_option_id"
        )
    selected = next(
        (option for option in challenge.options if option.id == selected_option_id), None
    )
    if selected is None:
        raise ChallengeOptionNotFoundError(selected_option_id)

    return GradedAnswer(
        correct=selected.correct,
        recorded_option_id=selected.id,
        submitted_option_ids=[selected.id],
        correct_option_ids=[option.id for option in challenge.options if option.correct],
    )


def grade_ordered(challenge: Challenge, selected_option_ids: list[str] | None) -> GradedAnswer:
    """Grade a word-ordering answer against the stored option order.

    Compares the *words*, not the option ids: a sentence can repeat a word
    ("càng ... càng ..."), and two tiles carrying the same text are
    interchangeable — swapping them still spells the right sentence, so
    marking that wrong would be marking a correct answer wrong.

    Every tile has to be used. A partial sequence that happens to prefix
    the solution is not the sentence.
    """
    if selected_option_ids is None:
        raise InvalidAnswerSubmissionError(
            "ORDER challenges are answered with selected_option_ids"
        )

    options_by_id = {option.id: option for option in challenge.options}
    for option_id in selected_option_ids:
        if option_id not in options_by_id:
            raise ChallengeOptionNotFoundError(option_id)
    if len(set(selected_option_ids)) != len(selected_option_ids):
        raise InvalidAnswerSubmissionError("An option may only be placed once")

    solution = sorted(challenge.options, key=lambda option: option.order_index)
    submitted_words = [options_by_id[option_id].text for option_id in selected_option_ids]
    correct = submitted_words == [option.text for option in solution]

    return GradedAnswer(
        correct=correct,
        # There is no single "selected option" to remember here; the row
        # records that the challenge was attempted and whether it was right.
        recorded_option_id=None,
        submitted_option_ids=list(selected_option_ids),
        # A sequence, not a set: this is the sentence in the right order.
        correct_option_ids=[option.id for option in solution],
    )


def answer_key(challenge: Challenge) -> list[str]:
    """The option ids this challenge is graded against.

    For a single-choice challenge that is its correct options -- normally one,
    and order means nothing. For an ORDER challenge every tile is flagged
    correct, so a set of them says nothing at all; the key carries the solution
    *in order* instead, which is what `order_index` spells out. An empty list
    either way means the challenge has no answer to grade against and must not
    be served.
    """
    if challenge.type is ChallengeType.ORDER:
        return [
            option.id for option in sorted(challenge.options, key=lambda o: o.order_index)
        ]
    return [option.id for option in challenge.options if option.correct]
