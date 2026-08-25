"""Bulk-import the quiz JSON files under data/quiz/ into a self-contained
"Ngan hang cau hoi" course so they're queryable through the existing
question-bank schema (Course -> Unit -> Lesson -> Challenge -> ChallengeOption)
and through QuizService's random-sample generation.

Writes directly through SQLAlchemy Core batched inserts instead of
CourseContentService, whose per-row duplicate-order_index checks are meant
for the admin API and are too slow for ~156k challenges / ~630k options.

Usage:
    conda run -n backend python -m scripts.import_quiz_bank [--reset]
"""

from __future__ import annotations

import argparse
import asyncio
import difflib
import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionFactory, engine
from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.models.content.challenge_option import ChallengeOption
from app.models.content.course import Course
from app.models.content.lesson import Lesson
from app.models.content.passage import Passage
from app.models.content.topic import Topic
from app.models.content.unit import Unit

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "quiz"
COURSE_TITLE = "Ngân hàng câu hỏi"
BATCH_SIZE = 5000
SKIPPED_LOG_PATH = DATA_DIR / "_import_skipped_common_error_corrections.json"
PUNCT = " .,!?;:\"'"

DIFFICULTY_MAP = {
    "easy": ChallengeDifficulty.EASY,
    "medium": ChallengeDifficulty.MEDIUM,
    "hard": ChallengeDifficulty.HARD,
    # ChallengeDifficulty only has 3 tiers; "expert" (only in
    # quiz_multiple_choice_4options.json, part P5/C1-C2) folds into HARD.
    "expert": ChallengeDifficulty.HARD,
}

TOPIC_GRAMMAR_FILL = "Ngữ pháp - Điền từ"
TOPIC_READING = "Đọc hiểu"
TOPIC_SENTENCE_BUILDER = "Ghép câu"
TOPIC_TRUE_FALSE = "Sửa lỗi Đúng/Sai"
TOPIC_EXPLANATIONS = "Ngữ pháp có giải thích"
TOPIC_COMMON_ERROR = "Lỗi thường gặp"
TOPIC_FIND_ERROR = "Tìm lỗi sai"

ALL_TOPICS = [
    TOPIC_GRAMMAR_FILL,
    TOPIC_READING,
    TOPIC_SENTENCE_BUILDER,
    TOPIC_TRUE_FALSE,
    TOPIC_EXPLANATIONS,
    TOPIC_COMMON_ERROR,
    TOPIC_FIND_ERROR,
]


def load_json(name: str) -> Any:
    return json.loads((DATA_DIR / name).read_text(encoding="utf-8"))


def _source_metadata(row: dict[str, Any]) -> dict[str, Any]:
    """Fields present (with the same names) across every source file:
    cefr_level/toeic_band/toeic_min_score always; tags only on the 3
    quiz_with_explanations.json sub-datasets (None elsewhere)."""
    return {
        "cefr_level": row.get("cefr_level"),
        "toeic_band": row.get("toeic_band"),
        "toeic_min_score": row.get("toeic_min_score"),
        "tags": row.get("tags"),
    }


# ---------------------------------------------------------------------------
# Per-source transforms. Each yields (challenge_fields, option_dicts) where
# option_dicts is [{"text": str, "correct": bool}, ...] in display order.
# challenge_fields never includes id/lesson_id/topic_id/order_index -- the
# driver fills those in.
# ---------------------------------------------------------------------------


def transform_multiple_choice_4options(
    rows: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for row in rows:
        options_map: dict[str, str] = row["options_map"]
        correct_key = row["correct_answer"]
        options = [
            {"text": text, "correct": key == correct_key} for key, text in options_map.items()
        ]
        explanation = (
            f"Câu hoàn chỉnh: {row['solved_sentence']}" if row.get("solved_sentence") else None
        )
        yield (
            {
                "type": ChallengeType.SELECT,
                "question": row["question"],
                "explanation": explanation,
                "difficulty": DIFFICULTY_MAP[row["difficulty"]],
                "source_ref": f"mc4:{row['id']}",
                **_source_metadata(row),
            },
            options,
        )


def transform_reading_comprehension(
    rows: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]]:
    for row in rows:
        options_map: dict[str, str] = row["options_map"]
        correct_key = row["correct_answer"]
        options = [
            {"text": text, "correct": key == correct_key} for key, text in options_map.items()
        ]
        passage_info = {
            "source_ref": row["example_id"],
            "content": row["article"],
            "level_grade": row["level_grade"],
        }
        yield (
            {
                "type": ChallengeType.SELECT,
                "question": row["question"],
                "explanation": None,
                "difficulty": DIFFICULTY_MAP[row["difficulty"]],
                "source_ref": f"reading:{row['id']}",
                **_source_metadata(row),
            },
            options,
            passage_info,
        )


def transform_sentence_builder(
    rows: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for row in rows:
        words: list[str] = row["correct_words"]
        if len(words) < 2:
            continue
        options = [{"text": word, "correct": True} for word in words]
        question = f'Dịch câu sau sang tiếng Việt: "{row["english"]}"'
        yield (
            {
                "type": ChallengeType.ASSIST,
                "question": question,
                "explanation": None,
                "difficulty": DIFFICULTY_MAP[row["difficulty"]],
                "source_ref": f"sentence_builder:{row['id']}",
                **_source_metadata(row),
            },
            options,
        )


def transform_true_false(
    rows: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for row in rows:
        has_error = row["has_error"]
        options = [
            {"text": "Câu đúng ngữ pháp", "correct": not has_error},
            {"text": "Câu sai ngữ pháp (có lỗi)", "correct": has_error},
        ]
        if has_error:
            explanation = (
                f"Câu đúng: {row['corrected_sentence']} "
                f"(Lỗi: {row['error_type']}, mức độ: {row['error_severity']})"
            )
        else:
            explanation = "Câu không có lỗi ngữ pháp."
        yield (
            {
                "type": ChallengeType.SELECT,
                "question": row["input_sentence"],
                "explanation": explanation,
                "difficulty": DIFFICULTY_MAP[row["difficulty"]],
                "source_ref": f"true_false:{row['id']}",
                **_source_metadata(row),
            },
            options,
        )


def transform_explanations_mcq(
    rows: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for row in rows:
        options_map: dict[str, str] = row["options_map"]
        correct_key = row["correct_answer"]
        options = [
            {"text": text, "correct": key == correct_key} for key, text in options_map.items()
        ]
        yield (
            {
                "type": ChallengeType.SELECT,
                "question": row["question"],
                "explanation": row.get("explanation"),
                "difficulty": DIFFICULTY_MAP[row["difficulty"]],
                "source_ref": f"explanations.mcq:{row['id']}",
                **_source_metadata(row),
            },
            options,
        )


def transform_grammar_rules_find_error(
    rows: list[dict[str, Any]],
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for row in rows:
        has_error = row["sentence"] != row["correct_version"]
        options = [
            {"text": "Câu này có lỗi sai", "correct": has_error},
            {"text": "Câu này không có lỗi sai", "correct": not has_error},
        ]
        explanation = row.get("explanation") or ""
        if has_error:
            explanation = f"{explanation} Sửa đúng: {row['correct_version']}".strip()
        yield (
            {
                "type": ChallengeType.SELECT,
                "question": row["sentence"],
                "explanation": explanation or None,
                "difficulty": DIFFICULTY_MAP[row["difficulty"]],
                "source_ref": f"explanations.find_error:{row['id']}",
                **_source_metadata(row),
            },
            options,
        )


def _char_diff_fragment(error_word: str, correct_word: str) -> str:
    matcher = difflib.SequenceMatcher(None, error_word, correct_word)
    fragment = ""
    for tag, _i1, _i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            fragment += correct_word[j1:j2]
    return fragment


def _derive_common_error_answer(
    error_sentence: str, correct_sentence: str, choices: list[str]
) -> str | None:
    """Find which `choices` entry turns error_sentence into correct_sentence.

    Word-level diff first (handles whole-word/phrase replacements and
    deletions, where "_" means "no word here"); falls back to a
    character-level diff of the single differing word pair for
    within-word fixes like "23th" -> "23rd" ("th" -> "rd"). Returns None
    when the diff can't be matched to exactly one choice.
    """
    error_tokens = error_sentence.split()
    correct_tokens = correct_sentence.split()
    matcher = difflib.SequenceMatcher(None, error_tokens, correct_tokens)
    diff_error: list[str] = []
    diff_correct: list[str] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag != "equal":
            diff_error.extend(error_tokens[i1:i2])
            diff_correct.extend(correct_tokens[j1:j2])

    choice_norms = {
        choice: ("" if choice == "_" else choice.strip(PUNCT).lower()) for choice in choices
    }

    def match(target: str) -> str | None:
        target = target.strip(PUNCT).lower()
        hits = [choice for choice, norm in choice_norms.items() if norm == target]
        return hits[0] if len(hits) == 1 else None

    answer = match(" ".join(diff_correct))
    if answer is not None:
        return answer
    if len(diff_error) == 1 and len(diff_correct) == 1:
        fragment = _char_diff_fragment(diff_error[0], diff_correct[0])
        return match(fragment)
    return None


def transform_common_error_corrections(
    rows: list[dict[str, Any]], skipped_ids: list[int]
) -> Iterator[tuple[dict[str, Any], list[dict[str, Any]]]]:
    for row in rows:
        answer = _derive_common_error_answer(
            row["error_sentence"], row["correct_sentence"], row["choices"]
        )
        if answer is None:
            skipped_ids.append(row["id"])
            continue
        options = [{"text": choice, "correct": choice == answer} for choice in row["choices"]]
        yield (
            {
                "type": ChallengeType.SELECT,
                "question": row["error_sentence"],
                "explanation": row.get("explanation"),
                "difficulty": DIFFICULTY_MAP[row["difficulty"]],
                "source_ref": f"explanations.common_error:{row['id']}",
                **_source_metadata(row),
            },
            options,
        )


# ---------------------------------------------------------------------------
# Driver
# ---------------------------------------------------------------------------


@dataclass
class Batch:
    challenges: list[dict[str, Any]] = None  # type: ignore[assignment]
    options: list[dict[str, Any]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        self.challenges = []
        self.options = []

    def add(self, challenge_row: dict[str, Any], option_dicts: list[dict[str, Any]]) -> None:
        challenge_id = challenge_row["id"]
        # SELECT has exactly one correct option; ASSIST (sentence builder)
        # marks every option correct, so joining them gives the assembled
        # correct sentence -- either way this reproduces the source
        # "correct_text" field without needing each transform to supply it.
        correct_texts = [option["text"] for option in option_dicts if option["correct"]]
        challenge_row["correct_text"] = " ".join(correct_texts) if correct_texts else None
        self.challenges.append(challenge_row)
        for index, option in enumerate(option_dicts, start=1):
            self.options.append(
                {
                    "id": str(uuid4()),
                    "challenge_id": challenge_id,
                    "text": option["text"],
                    "correct": option["correct"],
                    "order_index": index,
                    "image_src": None,
                    "audio_src": None,
                }
            )

    def is_full(self) -> bool:
        return len(self.challenges) >= BATCH_SIZE


async def _flush(session: AsyncSession, batch: Batch) -> None:
    if batch.challenges:
        await session.execute(insert(Challenge), batch.challenges)
    if batch.options:
        await session.execute(insert(ChallengeOption), batch.options)
    await session.commit()
    batch.challenges = []
    batch.options = []


async def _get_or_create_topics(session: AsyncSession) -> dict[str, str]:
    result = await session.execute(select(Topic).where(Topic.name.in_(ALL_TOPICS)))
    topics = {topic.name: topic.id for topic in result.scalars().all()}
    missing = [name for name in ALL_TOPICS if name not in topics]
    if missing:
        rows = [{"id": str(uuid4()), "name": name} for name in missing]
        await session.execute(insert(Topic), rows)
        await session.commit()
        for row in rows:
            topics[row["name"]] = row["id"]
    return topics


async def _create_unit_with_pool_lesson(
    session: AsyncSession, course_id: str, title: str, description: str, order_index: int
) -> str:
    unit_id = str(uuid4())
    lesson_id = str(uuid4())
    await session.execute(
        insert(Unit),
        [
            {
                "id": unit_id,
                "course_id": course_id,
                "title": title,
                "description": description,
                "order_index": order_index,
            }
        ],
    )
    await session.execute(
        insert(Lesson), [{"id": lesson_id, "unit_id": unit_id, "title": title, "order_index": 1}]
    )
    await session.commit()
    return lesson_id


async def _import_simple_group(
    session: AsyncSession,
    transform: Iterator[tuple[dict[str, Any], list[dict[str, Any]]]],
    lesson_id: str,
    topic_id: str,
) -> int:
    batch = Batch()
    order_index = 0
    for challenge_fields, option_dicts in transform:
        order_index += 1
        challenge_row = {
            "id": str(uuid4()),
            "lesson_id": lesson_id,
            "topic_id": topic_id,
            "passage_id": None,
            "order_index": order_index,
            **challenge_fields,
        }
        batch.add(challenge_row, option_dicts)
        if batch.is_full():
            await _flush(session, batch)
    await _flush(session, batch)
    return order_index


async def _import_reading_group(
    session: AsyncSession,
    transform: Iterator[tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]],
    lesson_id: str,
    topic_id: str,
) -> tuple[int, int]:
    batch = Batch()
    passage_batch: list[dict[str, Any]] = []
    passage_cache: dict[str, str] = {}
    order_index = 0
    passage_count = 0
    for challenge_fields, option_dicts, passage_info in transform:
        source_ref = passage_info["source_ref"]
        passage_id = passage_cache.get(source_ref)
        if passage_id is None:
            passage_id = str(uuid4())
            passage_cache[source_ref] = passage_id
            passage_batch.append(
                {
                    "id": passage_id,
                    "source_ref": source_ref,
                    "content": passage_info["content"],
                    "level_grade": passage_info["level_grade"],
                }
            )
            passage_count += 1

        order_index += 1
        challenge_row = {
            "id": str(uuid4()),
            "lesson_id": lesson_id,
            "topic_id": topic_id,
            "passage_id": passage_id,
            "order_index": order_index,
            **challenge_fields,
        }
        batch.add(challenge_row, option_dicts)
        if batch.is_full():
            # Every passage referenced by this challenge batch must be
            # committed first, or the FK insert below fails -- passages
            # can't be flushed independently on their own size threshold.
            if passage_batch:
                await session.execute(insert(Passage), passage_batch)
                await session.commit()
                passage_batch = []
            await _flush(session, batch)

    if passage_batch:
        await session.execute(insert(Passage), passage_batch)
        await session.commit()
    await _flush(session, batch)
    return order_index, passage_count


async def main(reset: bool) -> None:
    async with AsyncSessionFactory() as session:
        result = await session.execute(select(Course).where(Course.title == COURSE_TITLE))
        course = result.scalar_one_or_none()
        if course is not None and not reset:
            print(
                f"Course '{COURSE_TITLE}' already exists (id={course.id}). "
                "Pass --reset to wipe and reimport."
            )
            return
        if reset:
            # Passages aren't owned by the Course FK tree (Challenge ->
            # Passage is ondelete=SET NULL, not CASCADE), and this script is
            # the only writer of Passage rows, so a full reset always clears
            # them too -- otherwise re-inserting hits their source_ref
            # unique constraint even after the Course itself is gone.
            if course is not None:
                await session.delete(course)
                await session.commit()
                print("Existing course deleted.")
            await session.execute(delete(Passage))
            await session.commit()
            print("Cleared any existing passages.")

        topics = await _get_or_create_topics(session)

        course_id = str(uuid4())
        await session.execute(
            insert(Course),
            [{"id": course_id, "title": COURSE_TITLE, "image_src": "quiz-bank-cover.svg"}],
        )
        await session.commit()

        summary: list[tuple[str, int]] = []
        order_index = 0

        def next_order() -> int:
            nonlocal order_index
            order_index += 1
            return order_index

        # 1-5: multiple_choice_4options, one unit per part_name
        mc4_rows = load_json("quiz_multiple_choice_4options.json")
        mc4_by_part: dict[str, list[dict[str, Any]]] = {}
        for row in mc4_rows:
            mc4_by_part.setdefault(row["part_name"], []).append(row)
        for part_name in sorted(mc4_by_part, key=lambda p: mc4_by_part[p][0]["part"]):
            rows = mc4_by_part[part_name]
            lesson_id = await _create_unit_with_pool_lesson(
                session, course_id, f"Ngữ pháp - Điền từ ({part_name})", part_name, next_order()
            )
            count = await _import_simple_group(
                session,
                transform_multiple_choice_4options(rows),
                lesson_id,
                topics[TOPIC_GRAMMAR_FILL],
            )
            summary.append((part_name, count))

        # 6-7: reading comprehension, split into Middle/High grade units
        reading_rows: list[dict[str, Any]] = []
        for i in range(1, 6):
            reading_rows.extend(load_json(f"quiz_reading_comprehension_part{i}.json"))
        for level_grade, label in (("middle", "Middle Grade"), ("high", "High Grade")):
            rows = [row for row in reading_rows if row["level_grade"] == level_grade]
            lesson_id = await _create_unit_with_pool_lesson(
                session,
                course_id,
                f"Đọc hiểu - {label}",
                f"Reading comprehension - {label}",
                next_order(),
            )
            count, passage_count = await _import_reading_group(
                session, transform_reading_comprehension(rows), lesson_id, topics[TOPIC_READING]
            )
            summary.append((f"Đọc hiểu - {label} ({passage_count} passages)", count))

        # 8-10: sentence builder, one unit per part_name
        sb_rows = load_json("quiz_sentence_builder.json")
        sb_by_part: dict[str, list[dict[str, Any]]] = {}
        for row in sb_rows:
            sb_by_part.setdefault(row["part_name"], []).append(row)
        for part_name, rows in sb_by_part.items():
            lesson_id = await _create_unit_with_pool_lesson(
                session, course_id, f"Ghép câu - {part_name}", part_name, next_order()
            )
            count = await _import_simple_group(
                session,
                transform_sentence_builder(rows),
                lesson_id,
                topics[TOPIC_SENTENCE_BUILDER],
            )
            summary.append((part_name, count))

        # 11-13: true/false, one unit per part_name
        tf_rows = load_json("quiz_true_false.json")
        tf_by_part: dict[str, list[dict[str, Any]]] = {}
        for row in tf_rows:
            tf_by_part.setdefault(row["part_name"], []).append(row)
        for part_name, rows in tf_by_part.items():
            lesson_id = await _create_unit_with_pool_lesson(
                session,
                course_id,
                f"Sửa lỗi Đúng/Sai - {part_name}",
                part_name,
                next_order(),
            )
            count = await _import_simple_group(
                session, transform_true_false(rows), lesson_id, topics[TOPIC_TRUE_FALSE]
            )
            summary.append((part_name, count))

        # 14-16: quiz_with_explanations.json (3 sub-kinds)
        explanations = load_json("quiz_with_explanations.json")

        lesson_id = await _create_unit_with_pool_lesson(
            session,
            course_id,
            "Ngữ pháp có giải thích",
            "Rules Quiz with Explanations",
            next_order(),
        )
        count = await _import_simple_group(
            session,
            transform_explanations_mcq(explanations["multiple_choice_quizzes"]),
            lesson_id,
            topics[TOPIC_EXPLANATIONS],
        )
        summary.append(("Ngữ pháp có giải thích", count))

        skipped_ids: list[int] = []
        lesson_id = await _create_unit_with_pool_lesson(
            session, course_id, "Lỗi thường gặp", "Common Mistakes in English", next_order()
        )
        count = await _import_simple_group(
            session,
            transform_common_error_corrections(
                explanations["common_error_corrections"], skipped_ids
            ),
            lesson_id,
            topics[TOPIC_COMMON_ERROR],
        )
        summary.append((f"Lỗi thường gặp ({len(skipped_ids)} skipped)", count))
        if skipped_ids:
            SKIPPED_LOG_PATH.write_text(json.dumps(skipped_ids, indent=2), encoding="utf-8")

        lesson_id = await _create_unit_with_pool_lesson(
            session, course_id, "Tìm lỗi sai", "Error Identification Rules", next_order()
        )
        count = await _import_simple_group(
            session,
            transform_grammar_rules_find_error(explanations["grammar_rules_find_error"]),
            lesson_id,
            topics[TOPIC_FIND_ERROR],
        )
        summary.append(("Tìm lỗi sai", count))

        print("\nImport summary:")
        total = 0
        for label, count in summary:
            print(f"  {label}: {count}")
            total += count
        print(f"  TOTAL: {total}")
        if skipped_ids:
            print(
                f"\n{len(skipped_ids)} common_error_corrections rows skipped "
                f"(ambiguous correct answer) -> {SKIPPED_LOG_PATH}"
            )

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete the existing 'Ngân hàng câu hỏi' course (if any) and reimport from scratch.",
    )
    args = parser.parse_args()
    asyncio.run(main(reset=args.reset))
