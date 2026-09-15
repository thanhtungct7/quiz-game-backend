"""Weave the "4000 Essential English Words" deck into the learn path.

The deck's 3600 numbered words (Books 1-6) become vocabulary units of the
existing "Ngan hang cau hoi" course, alternating with its TOEIC units:

    TOEIC 250-450 · Chặng 1, Từ vựng Book 1 · Chặng 1, TOEIC 250-450 · Chặng 2, ...

A separate course would stay invisible -- the app opens the first course -- and
units appended after the last TOEIC band would never be reached.

Layout, in order of importance:

1. One lesson holds `WORDS_PER_LESSON` questions of a single kind.
2. Each 20-word unit of the book yields six lessons, its words taken ten at a
   time: picture, definition, example, picture, definition, example. Two
   neighbouring lessons are therefore never the same kind.
3. Lessons fill path units of `LESSONS_PER_UNIT`, book by book: 30 book units x
   6 lessons = 180 lessons = 9 path units per book.

Every question is a SELECT with four options. The three wrong ones are other
words of the same book unit, so they sit at the same level:

- picture:    'Chọn hình ảnh cho từ "backpack"'  -> four images
- definition: the definition with the word blanked -> four words, each playable
- example:    the example sentence with the word blanked -> four inflected forms

Media paths point into Firebase Storage (see scripts.firebase_vocab_media).

Existing lessons keep their ids, so learners keep their progress; only unit
`order_index` changes. --remove takes the vocabulary units back out (and with
them any progress made in them) and closes the gaps.

Usage:
    conda run -n backend python -m scripts.import_vocab_deck [--remove] [--seed N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionFactory, engine
from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.models.content.challenge_option import ChallengeOption
from app.models.content.course import Course
from app.models.content.lesson import Lesson
from app.models.content.topic import Topic
from app.models.content.unit import Unit
from scripts.import_quiz_bank import COURSE_TITLE

VOCAB_PATH = Path(__file__).resolve().parent.parent / "data" / "vocab" / "vocab_4000.json"
TOPIC_NAME = "Từ vựng"
UNIT_TITLE_PREFIX = "Từ vựng Book"
SOURCE_PREFIX = "eew"
LESSONS_PER_UNIT = 20
WORDS_PER_LESSON = 10
OPTIONS_PER_QUESTION = 4
DEFAULT_SEED = 20260915
# Units are moved above this while being renumbered, so no two ever share an
# order_index mid-way (uq_units_course_id_order_index).
ORDER_OFFSET = 10_000
BATCH_SIZE = 5000

KIND_PICTURE = "picture"
KIND_DEFINITION = "definition"
KIND_EXAMPLE = "example"
LESSON_CYCLE = (KIND_PICTURE, KIND_DEFINITION, KIND_EXAMPLE)
LABEL_OF_KIND = {
    KIND_PICTURE: "Chọn hình",
    KIND_DEFINITION: "Định nghĩa",
    KIND_EXAMPLE: "Điền câu",
}
# Rough levels for the six books; the deck itself names none.
CEFR_OF_BOOK = {1: "A1", 2: "A2", 3: "B1", 4: "B1", 5: "B2", 6: "C1"}
DIFFICULTY_OF_BOOK = {
    1: ChallengeDifficulty.EASY,
    2: ChallengeDifficulty.EASY,
    3: ChallengeDifficulty.MEDIUM,
    4: ChallengeDifficulty.MEDIUM,
    5: ChallengeDifficulty.HARD,
    6: ChallengeDifficulty.HARD,
}

Word = dict[str, Any]


@dataclass(frozen=True)
class Option:
    text: str
    correct: bool
    image_src: str | None = None
    audio_src: str | None = None


@dataclass(frozen=True)
class Question:
    note_id: int
    kind: str
    book: int
    question: str
    options: tuple[Option, ...]
    explanation: str

    @property
    def correct_text(self) -> str:
        return next(option.text for option in self.options if option.correct)


@dataclass
class PlannedLesson:
    title: str
    kind: str
    questions: list[Question]


@dataclass
class PlannedUnit:
    book: int
    number: int
    lessons: list[PlannedLesson] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"{UNIT_TITLE_PREFIX} {self.book} · Chặng {self.number}"

    @property
    def description(self) -> str:
        words: list[str] = []
        for lesson in self.lessons:
            for question in lesson.questions:
                word = _headword(question)
                if word not in words:
                    words.append(word)
        return (
            f"4000 Essential Words · CEFR {CEFR_OF_BOOK[self.book]} · "
            f"{', '.join(words[:4])}, …"
        )


def _headword(question: Question) -> str:
    return question.explanation.split(" /", 1)[0].split(" — ", 1)[0]


def load_words(path: Path = VOCAB_PATH) -> list[Word]:
    """The numbered words of Books 1-6, in book order. The Extra deck is left out."""
    with path.open(encoding="utf-8") as f:
        words = [word for word in json.load(f) if word["book"] is not None]
    return sorted(words, key=lambda word: word["index"])


def _explanation(word: Word) -> str:
    parts = [f"{word['word']} /{word['ipa']}/" if word["ipa"] else word["word"]]
    parts += [text for text in (word["meaning"], word["example"]) if text]
    return " — ".join(parts)


def _answer_text(word: Word, kind: str) -> str | None:
    return word["example_answer"] if kind == KIND_EXAMPLE else word["word"]


def build_question(word: Word, kind: str, unit_words: Sequence[Word], rng: Random) -> Question:
    """One question about `word`, with three wrong answers from `unit_words`.

    A word with no usable example sentence is asked by its definition instead,
    so an example lesson still holds a full set of questions.
    """
    if kind == KIND_EXAMPLE and not word["example_masked"]:
        kind = KIND_DEFINITION

    correct = _answer_text(word, kind)
    assert correct is not None
    seen = {correct.lower()}
    candidates: list[Word] = []
    for other in unit_words:
        text = _answer_text(other, kind)
        if other is word or not text or text.lower() in seen:
            continue
        if kind == KIND_PICTURE and not other["image"]:
            continue
        seen.add(text.lower())
        candidates.append(other)
    if len(candidates) < OPTIONS_PER_QUESTION - 1:
        raise ValueError(f"not enough distinct wrong answers for {word['word']!r} ({kind})")

    chosen = [word, *rng.sample(candidates, OPTIONS_PER_QUESTION - 1)]
    rng.shuffle(chosen)

    def option(of: Word) -> Option:
        text = _answer_text(of, kind)
        assert text is not None
        return Option(
            text=text,
            correct=of is word,
            image_src=of["image"]["path"] if kind == KIND_PICTURE else None,
            audio_src=(
                of["audio_word"]["path"] if kind == KIND_DEFINITION and of["audio_word"] else None
            ),
        )

    if kind == KIND_PICTURE:
        prompt = f'Chọn hình ảnh cho từ "{word["word"]}"'
    elif kind == KIND_DEFINITION:
        prompt = word["meaning_masked"]
    else:
        prompt = word["example_masked"]

    return Question(
        note_id=word["note_id"],
        kind=kind,
        book=word["book"],
        question=prompt,
        options=tuple(option(of) for of in chosen),
        explanation=_explanation(word),
    )


def plan_book(book: int, words: Sequence[Word], rng: Random) -> list[PlannedUnit]:
    """Lay one book's words out as path units."""
    by_unit: dict[int, list[Word]] = {}
    for word in words:
        by_unit.setdefault(word["unit"], []).append(word)

    lessons: list[PlannedLesson] = []
    kind_count = dict.fromkeys(LESSON_CYCLE, 0)
    for unit_number in sorted(by_unit):
        unit_words = by_unit[unit_number]
        for start in range(0, len(unit_words), WORDS_PER_LESSON):
            group = unit_words[start : start + WORDS_PER_LESSON]
            for kind in LESSON_CYCLE:
                kind_count[kind] += 1
                lessons.append(
                    PlannedLesson(
                        title=f"{LABEL_OF_KIND[kind]} {kind_count[kind]}",
                        kind=kind,
                        questions=[build_question(word, kind, unit_words, rng) for word in group],
                    )
                )

    units: list[PlannedUnit] = []
    for start in range(0, len(lessons), LESSONS_PER_UNIT):
        units.append(
            PlannedUnit(
                book=book,
                number=len(units) + 1,
                lessons=lessons[start : start + LESSONS_PER_UNIT],
            )
        )
    return units


def plan_units(words: Sequence[Word], seed: int = DEFAULT_SEED) -> list[PlannedUnit]:
    rng = Random(seed)  # noqa: S311 -- picking quiz distractors, not security
    by_book: dict[int, list[Word]] = {}
    for word in words:
        by_book.setdefault(word["book"], []).append(word)
    return [unit for book in sorted(by_book) for unit in plan_book(book, by_book[book], rng)]


def interleave[T](first: Iterable[T], second: Iterable[T]) -> list[T]:
    """a1, b1, a2, b2, ... with whatever is left of the longer one at the end."""
    left, right = list(first), list(second)
    merged: list[T] = []
    for index in range(max(len(left), len(right))):
        if index < len(left):
            merged.append(left[index])
        if index < len(right):
            merged.append(right[index])
    return merged


# --- database ------------------------------------------------------------


async def _course_id(session: AsyncSession) -> str:
    course = (
        await session.execute(select(Course).where(Course.title == COURSE_TITLE))
    ).scalar_one_or_none()
    if course is None:
        raise SystemExit(f"Course '{COURSE_TITLE}' not found. Run scripts.import_quiz_bank first.")
    return course.id


def _is_vocab_unit(title: str) -> bool:
    return title.startswith(UNIT_TITLE_PREFIX)


async def _units_in_order(session: AsyncSession, course_id: str) -> list[tuple[str, str]]:
    rows = await session.execute(
        select(Unit.id, Unit.title).where(Unit.course_id == course_id).order_by(Unit.order_index)
    )
    return [(row.id, row.title) for row in rows]


async def _renumber(session: AsyncSession, course_id: str, unit_ids: Sequence[str]) -> None:
    """Give `unit_ids` the order 1..n, in two passes so no step hits the unique key."""
    await session.execute(
        update(Unit)
        .where(Unit.course_id == course_id)
        .values(order_index=Unit.order_index + ORDER_OFFSET)
    )
    for order_index, unit_id in enumerate(unit_ids, start=1):
        await session.execute(
            update(Unit).where(Unit.id == unit_id).values(order_index=order_index)
        )


async def _topic_id(session: AsyncSession) -> str:
    topic_id = (
        await session.execute(select(Topic.id).where(Topic.name == TOPIC_NAME))
    ).scalar_one_or_none()
    if topic_id is None:
        topic_id = str(uuid4())
        await session.execute(insert(Topic), [{"id": topic_id, "name": TOPIC_NAME}])
    return topic_id


def _rows(
    course_id: str, units: Sequence[PlannedUnit], topic_id: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    unit_rows: list[dict[str, Any]] = []
    lesson_rows: list[dict[str, Any]] = []
    challenge_rows: list[dict[str, Any]] = []
    option_rows: list[dict[str, Any]] = []
    for unit in units:
        unit_id = str(uuid4())
        # order_index is placeholder here: every unit is renumbered afterwards.
        unit_rows.append(
            {
                "id": unit_id,
                "course_id": course_id,
                "title": unit.title,
                "description": unit.description,
                "order_index": 2 * ORDER_OFFSET + len(unit_rows),
            }
        )
        for lesson_order, lesson in enumerate(unit.lessons, start=1):
            lesson_id = str(uuid4())
            lesson_rows.append(
                {
                    "id": lesson_id,
                    "unit_id": unit_id,
                    "title": lesson.title,
                    "order_index": lesson_order,
                    "is_bank": False,
                }
            )
            for question_order, question in enumerate(lesson.questions, start=1):
                challenge_id = str(uuid4())
                challenge_rows.append(
                    {
                        "id": challenge_id,
                        "lesson_id": lesson_id,
                        "type": ChallengeType.SELECT,
                        "question": question.question,
                        "explanation": question.explanation,
                        "difficulty": DIFFICULTY_OF_BOOK[question.book],
                        "order_index": question_order,
                        "topic_id": topic_id,
                        "passage_id": None,
                        "source_ref": f"{SOURCE_PREFIX}:{question.note_id}:{question.kind}",
                        "correct_text": question.correct_text,
                        "tags": ["vocabulary", question.kind],
                        "cefr_level": CEFR_OF_BOOK[question.book],
                        "toeic_band": None,
                        "toeic_min_score": None,
                    }
                )
                for option_order, option in enumerate(question.options, start=1):
                    option_rows.append(
                        {
                            "id": str(uuid4()),
                            "challenge_id": challenge_id,
                            "text": option.text,
                            "correct": option.correct,
                            "order_index": option_order,
                            "image_src": option.image_src,
                            "audio_src": option.audio_src,
                        }
                    )
    return unit_rows, lesson_rows, challenge_rows, option_rows


async def _insert(session: AsyncSession, model: type[Any], rows: list[dict[str, Any]]) -> None:
    for start in range(0, len(rows), BATCH_SIZE):
        await session.execute(insert(model), rows[start : start + BATCH_SIZE])


async def import_deck(seed: int) -> None:
    units = plan_units(load_words(), seed)
    async with AsyncSessionFactory() as session:
        course_id = await _course_id(session)
        existing = await _units_in_order(session, course_id)
        if any(_is_vocab_unit(title) for _, title in existing):
            raise SystemExit("Vocabulary units are already on the path. Run with --remove first.")

        topic_id = await _topic_id(session)
        unit_rows, lesson_rows, challenge_rows, option_rows = _rows(course_id, units, topic_id)
        await _insert(session, Unit, unit_rows)
        await _insert(session, Lesson, lesson_rows)
        await _insert(session, Challenge, challenge_rows)
        await _insert(session, ChallengeOption, option_rows)

        path = interleave([unit_id for unit_id, _ in existing], [row["id"] for row in unit_rows])
        await _renumber(session, course_id, path)
        # One transaction: a failure anywhere leaves the path as it was.
        await session.commit()

    print(
        f"Added {len(unit_rows)} vocabulary units, {len(lesson_rows)} lessons, "
        f"{len(challenge_rows)} questions, {len(option_rows)} options; "
        f"the path now has {len(path)} units."
    )


async def remove_deck() -> None:
    async with AsyncSessionFactory() as session:
        course_id = await _course_id(session)
        existing = await _units_in_order(session, course_id)
        vocab_ids = [unit_id for unit_id, title in existing if _is_vocab_unit(title)]
        if not vocab_ids:
            print("No vocabulary units on the path.")
            return
        # Lessons, challenges, options and progress in them go with the units (ON DELETE CASCADE).
        await session.execute(delete(Unit).where(Unit.id.in_(vocab_ids)))
        remaining = [unit_id for unit_id, title in existing if not _is_vocab_unit(title)]
        await _renumber(session, course_id, remaining)
        await session.commit()
    print(f"Removed {len(vocab_ids)} vocabulary units; {len(remaining)} units renumbered 1..{len(remaining)}.")


async def main(remove: bool, seed: int) -> None:
    try:
        if remove:
            await remove_deck()
        else:
            await import_deck(seed)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--remove",
        action="store_true",
        help="Take the vocabulary units back off the path. Progress made in them is lost.",
    )
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED, help="Distractor shuffle seed.")
    args = parser.parse_args()
    asyncio.run(main(args.remove, args.seed))
