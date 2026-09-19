import re
from itertools import pairwise
from random import Random
from typing import Any

import pytest

from scripts.import_vocab_deck import (
    KIND_DEFINITION,
    KIND_EXAMPLE,
    KIND_PICTURE,
    LESSONS_PER_UNIT,
    VOCAB_PATH,
    WORDS_PER_LESSON,
    build_question,
    interleave,
    load_words,
    plan_units,
)


def _word(book: int, index: int) -> dict[str, Any]:
    unit = (index - (book - 1) * 600 - 1) // 20 + 1
    name = f"word{index}"
    stem = f"{book:02d}_{index:04d}"
    return {
        "note_id": index,
        "book": book,
        "unit": unit,
        "index": index,
        "word": name,
        "ipa": "wɜːd",
        "meaning": f"A {name} is a thing.",
        "meaning_masked": "A ___ is a thing.",
        "example": f"I like the {name}ed one.",
        "example_masked": "I like the ___ one.",
        "example_answer": f"{name}ed",
        "image": {"path": f"vocab/images/{stem}.jpg", "anki_file": str(index)},
        "audio_word": {"path": f"vocab/audio/{stem}.mp3", "anki_file": str(index)},
        "audio_meaning": None,
        "audio_example": None,
    }


def _book(book: int) -> list[dict[str, Any]]:
    first = (book - 1) * 600 + 1
    return [_word(book, index) for index in range(first, first + 600)]


def test_a_book_becomes_nine_full_units() -> None:
    units = plan_units(_book(1))
    assert len(units) == 9
    assert [unit.title for unit in units[:2]] == [
        "Từ vựng Book 1 · Chặng 1",
        "Từ vựng Book 1 · Chặng 2",
    ]
    for unit in units:
        assert len(unit.lessons) == LESSONS_PER_UNIT
        assert all(len(lesson.questions) == WORDS_PER_LESSON for lesson in unit.lessons)


def test_neighbouring_lessons_are_never_the_same_kind() -> None:
    lessons = [lesson for unit in plan_units(_book(1) + _book(2)) for lesson in unit.lessons]
    assert all(a.kind != b.kind for a, b in pairwise(lessons))


def test_every_word_is_asked_once_in_each_kind() -> None:
    lessons = [lesson for unit in plan_units(_book(1)) for lesson in unit.lessons]
    for kind in (KIND_PICTURE, KIND_DEFINITION, KIND_EXAMPLE):
        asked = [q.note_id for lesson in lessons if lesson.kind == kind for q in lesson.questions]
        assert sorted(asked) == list(range(1, 601))


def test_each_question_has_four_distinct_options_and_one_answer() -> None:
    for unit in plan_units(_book(1)):
        for lesson in unit.lessons:
            for question in lesson.questions:
                texts = [option.text.lower() for option in question.options]
                assert len(texts) == 4
                assert len(set(texts)) == 4
                assert sum(option.correct for option in question.options) == 1


def test_options_carry_the_media_their_kind_needs() -> None:
    words = _book(1)[:20]
    rng = Random(1)  # noqa: S311 - deterministic test fixture
    picture = build_question(words[0], KIND_PICTURE, words, rng)
    assert all(option.image_src and not option.audio_src for option in picture.options)
    definition = build_question(words[0], KIND_DEFINITION, words, rng)
    assert all(option.audio_src and not option.image_src for option in definition.options)
    example = build_question(words[0], KIND_EXAMPLE, words, rng)
    assert all(not option.image_src and not option.audio_src for option in example.options)
    assert example.correct_text == "word1ed"


def test_the_picture_prompt_names_the_word_and_its_option_shows_that_words_image() -> None:
    words = _book(1)[:20]
    question = build_question(words[3], KIND_PICTURE, words, Random(1))  # noqa: S311
    assert question.question == 'Chọn hình ảnh cho từ "word4"'
    correct = next(option for option in question.options if option.correct)
    assert correct.image_src == "vocab/images/01_0004.jpg"


def test_a_word_without_an_example_is_asked_by_its_definition() -> None:
    words = _book(1)[:20]
    words[0]["example_masked"] = None
    words[0]["example_answer"] = None
    question = build_question(words[0], KIND_EXAMPLE, words, Random(1))  # noqa: S311
    assert question.kind == KIND_DEFINITION
    assert question.question == "A ___ is a thing."


def test_the_same_seed_gives_the_same_path() -> None:
    def snapshot(seed: int) -> list[tuple[str, tuple[str, ...]]]:
        return [
            (q.question, tuple(option.text for option in q.options))
            for unit in plan_units(_book(1), seed)
            for lesson in unit.lessons
            for q in lesson.questions
        ]

    assert snapshot(7) == snapshot(7)
    assert snapshot(7) != snapshot(8)


def test_interleave_alternates_and_keeps_the_longer_tail() -> None:
    assert interleave(["T1", "T2", "T3"], ["V1", "V2"]) == ["T1", "V1", "T2", "V2", "T3"]
    assert interleave(["T1"], ["V1", "V2"]) == ["T1", "V1", "V2"]


@pytest.mark.skipif(not VOCAB_PATH.exists(), reason="run scripts.convert first")
def test_the_real_deck_plans_cleanly() -> None:
    units = plan_units(load_words())
    assert len(units) == 54
    for unit in units:
        assert len(unit.lessons) == LESSONS_PER_UNIT
        for lesson in unit.lessons:
            assert len(lesson.questions) == WORDS_PER_LESSON
            for question in lesson.questions:
                assert len({option.text.lower() for option in question.options}) == 4
                if question.kind in (KIND_DEFINITION, KIND_EXAMPLE):
                    # The prompt must not name the answer anywhere the blank does not.
                    answer = re.escape(question.correct_text)
                    unbounded = rf"(?<![A-Za-z]){answer}(?![A-Za-z])"
                    assert not re.search(unbounded, question.question, re.I)
