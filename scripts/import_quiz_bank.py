"""Bulk-import the quiz JSON files under data/quiz/ into a self-contained
"Ngan hang cau hoi" course so they're queryable through the existing
question-bank schema (Course -> Unit -> Lesson -> Challenge -> ChallengeOption)
and through QuizService's random-sample generation.

Each source file is one "group". A group is laid out as a stretch of the
learning path -- a few units, each of `--lessons-per-unit` lessons holding
`--challenges-per-lesson` questions -- and everything past that budget goes
into a single `is_bank=True` lesson. Bank lessons are hidden from the course
tree but still feed duo matches and practice draws, which sample the whole
`challenges` table. This is what keeps a lesson finishable: the bank holds
~156k questions, while a lesson holds ten.

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
from collections.abc import Iterable, Iterator
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
BANK_LESSON_TITLE = "Ngân hàng câu hỏi"
# Roughly Duolingo-sized: ~1000 lessons of ten questions across the whole course.
DEFAULT_CHALLENGES_PER_LESSON = 10
DEFAULT_LESSONS_PER_UNIT = 20
DEFAULT_UNITS_PER_GROUP = 3
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


@dataclass(frozen=True)
class PathLayout:
    """How much of a source group becomes walkable path, and in what shape."""

    challenges_per_lesson: int = DEFAULT_CHALLENGES_PER_LESSON
    lessons_per_unit: int = DEFAULT_LESSONS_PER_UNIT
    units_per_group: int = DEFAULT_UNITS_PER_GROUP

    @property
    def path_capacity(self) -> int:
        """Challenges a single group may contribute to the path. Everything the
        group holds beyond this lands in its bank lesson."""
        return self.challenges_per_lesson * self.lessons_per_unit * self.units_per_group


@dataclass
class GroupStats:
    units: int = 0
    path_lessons: int = 0
    path_challenges: int = 0
    bank_challenges: int = 0
    passages: int = 0

    @property
    def total_challenges(self) -> int:
        return self.path_challenges + self.bank_challenges


# One imported question: challenge fields, its options in display order, and
# the passage it belongs to (reading comprehension only).
SourceItem = tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]


def without_passages(
    transform: Iterable[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> Iterator[SourceItem]:
    for challenge_fields, option_dicts in transform:
        yield challenge_fields, option_dicts, None


class _GroupWriter:
    """Streams one source group into path lessons, then into a bank lesson.

    Single pass: units and lessons are created as they fill up, so the caller
    never has to know a group's size up front. Once `path_capacity` challenges
    have been placed, everything remaining goes to the bank lesson.
    """

    def __init__(
        self,
        session: AsyncSession,
        course_id: str,
        title: str,
        description: str,
        topic_id: str,
        layout: PathLayout,
        first_unit_order: int,
    ) -> None:
        self.session = session
        self.course_id = course_id
        self.title = title
        self.description = description
        self.topic_id = topic_id
        self.layout = layout
        self.next_unit_order = first_unit_order

        self.stats = GroupStats()
        self.batch = Batch()
        self.passage_batch: list[dict[str, Any]] = []
        self.passage_cache: dict[str, str] = {}

        self.unit_ids: list[str] = []
        self.lesson_id: str | None = None
        self.lesson_fill = 0
        self.path_open = True
        self.bank_lesson_id: str | None = None
        # Sentinel: no passage seen yet, so the first item is a passage boundary.
        self.current_passage_ref: str | None = ""

    # --- Structure ----------------------------------------------------------

    async def _create_unit(self) -> str:
        unit_id = str(uuid4())
        index = len(self.unit_ids) + 1
        title = self.title if self.layout.units_per_group == 1 else f"{self.title} · Chặng {index}"
        await self.session.execute(
            insert(Unit),
            [
                {
                    "id": unit_id,
                    "course_id": self.course_id,
                    "title": title[:100],
                    "description": self.description,
                    "order_index": self.next_unit_order,
                }
            ],
        )
        await self.session.commit()
        self.unit_ids.append(unit_id)
        self.next_unit_order += 1
        self.stats.units += 1
        return unit_id

    async def _open_path_lesson(self) -> bool:
        """Start the next path lesson. False once the group's path budget is spent."""
        if self.stats.path_challenges >= self.layout.path_capacity:
            return False
        if self.stats.path_lessons % self.layout.lessons_per_unit == 0:
            await self._create_unit()

        order_in_unit = self.stats.path_lessons % self.layout.lessons_per_unit + 1
        lesson_id = str(uuid4())
        await self.session.execute(
            insert(Lesson),
            [
                {
                    "id": lesson_id,
                    "unit_id": self.unit_ids[-1],
                    "title": f"Cửa {order_in_unit}",
                    "order_index": order_in_unit,
                    "is_bank": False,
                }
            ],
        )
        await self.session.commit()
        self.lesson_id = lesson_id
        self.lesson_fill = 0
        self.stats.path_lessons += 1
        return True

    async def _open_bank_lesson(self) -> str:
        """The group's overflow lesson, created on first use.

        It sits in the group's last unit at an order_index past every path
        lesson, so it can never collide with one.
        """
        if self.bank_lesson_id is None:
            if not self.unit_ids:
                await self._create_unit()
            self.bank_lesson_id = str(uuid4())
            await self.session.execute(
                insert(Lesson),
                [
                    {
                        "id": self.bank_lesson_id,
                        "unit_id": self.unit_ids[-1],
                        "title": BANK_LESSON_TITLE,
                        "order_index": self.layout.lessons_per_unit + 1,
                        "is_bank": True,
                    }
                ],
            )
            await self.session.commit()
        return self.bank_lesson_id

    # --- Content ------------------------------------------------------------

    def _passage_id(self, passage_info: dict[str, Any] | None) -> str | None:
        if passage_info is None:
            return None
        source_ref = passage_info["source_ref"]
        passage_id = self.passage_cache.get(source_ref)
        if passage_id is None:
            passage_id = str(uuid4())
            self.passage_cache[source_ref] = passage_id
            self.passage_batch.append(
                {
                    "id": passage_id,
                    "source_ref": source_ref,
                    "content": passage_info["content"],
                    "level_grade": passage_info["level_grade"],
                }
            )
            self.stats.passages += 1
        return passage_id

    async def add(self, item: SourceItem) -> None:
        challenge_fields, option_dicts, passage_info = item
        passage_ref = passage_info["source_ref"] if passage_info is not None else None
        # A reading passage's questions must stay in one lesson, so a lesson can
        # only roll over at a passage boundary -- which lets a reading lesson run
        # a little past challenges_per_lesson rather than splitting an article.
        at_passage_boundary = passage_ref is None or passage_ref != self.current_passage_ref
        self.current_passage_ref = passage_ref

        needs_lesson = self.lesson_id is None or (
            self.lesson_fill >= self.layout.challenges_per_lesson and at_passage_boundary
        )
        if self.path_open and needs_lesson:
            self.path_open = await self._open_path_lesson()

        if self.path_open:
            lesson_id = self.lesson_id
            self.lesson_fill += 1
            self.stats.path_challenges += 1
            order_index = self.lesson_fill
        else:
            lesson_id = await self._open_bank_lesson()
            self.stats.bank_challenges += 1
            order_index = self.stats.bank_challenges

        self.batch.add(
            {
                "id": str(uuid4()),
                "lesson_id": lesson_id,
                "topic_id": self.topic_id,
                "passage_id": self._passage_id(passage_info),
                "order_index": order_index,
                **challenge_fields,
            },
            option_dicts,
        )
        if self.batch.is_full():
            await self.flush()

    async def flush(self) -> None:
        # Every passage referenced by this challenge batch must be committed
        # first, or the FK insert fails -- passages can't be flushed
        # independently on their own size threshold.
        if self.passage_batch:
            await self.session.execute(insert(Passage), self.passage_batch)
            await self.session.commit()
            self.passage_batch = []
        await _flush(self.session, self.batch)


async def import_group(
    session: AsyncSession,
    course_id: str,
    title: str,
    description: str,
    topic_id: str,
    items: Iterable[SourceItem],
    layout: PathLayout,
    first_unit_order: int,
) -> GroupStats:
    writer = _GroupWriter(
        session, course_id, title, description, topic_id, layout, first_unit_order
    )
    for item in items:
        await writer.add(item)
    await writer.flush()
    return writer.stats


@dataclass
class SourceGroup:
    """One source file (or one part of one) laid out as its own stretch of path."""

    title: str
    description: str
    topic_id: str
    items: Iterable[SourceItem]
    label: str = ""
    extra: str = ""

    def __post_init__(self) -> None:
        if not self.label:
            self.label = self.title


def _build_groups(topics: dict[str, str], skipped_ids: list[int]) -> list[SourceGroup]:
    """Every source group in path order, easiest sources first."""
    groups: list[SourceGroup] = []

    # 1-5: multiple_choice_4options, one group per part_name (A1 -> C2).
    mc4_by_part: dict[str, list[dict[str, Any]]] = {}
    for row in load_json("quiz_multiple_choice_4options.json"):
        mc4_by_part.setdefault(row["part_name"], []).append(row)
    for part_name in sorted(mc4_by_part, key=lambda p: mc4_by_part[p][0]["part"]):
        rows = mc4_by_part[part_name]
        groups.append(
            SourceGroup(
                title=f"Ngữ pháp - Điền từ ({part_name})",
                description=part_name,
                topic_id=topics[TOPIC_GRAMMAR_FILL],
                items=without_passages(transform_multiple_choice_4options(rows)),
                label=part_name,
            )
        )

    # 6-7: reading comprehension, split into Middle/High grade groups.
    reading_rows: list[dict[str, Any]] = []
    for i in range(1, 6):
        reading_rows.extend(load_json(f"quiz_reading_comprehension_part{i}.json"))
    for level_grade, level_label in (("middle", "Middle Grade"), ("high", "High Grade")):
        rows = [row for row in reading_rows if row["level_grade"] == level_grade]
        groups.append(
            SourceGroup(
                title=f"Đọc hiểu - {level_label}",
                description=f"Reading comprehension - {level_label}",
                topic_id=topics[TOPIC_READING],
                items=transform_reading_comprehension(rows),
            )
        )

    # 8-10: sentence builder, one group per part_name.
    sb_by_part: dict[str, list[dict[str, Any]]] = {}
    for row in load_json("quiz_sentence_builder.json"):
        sb_by_part.setdefault(row["part_name"], []).append(row)
    for part_name, rows in sb_by_part.items():
        groups.append(
            SourceGroup(
                title=f"Ghép câu - {part_name}",
                description=part_name,
                topic_id=topics[TOPIC_SENTENCE_BUILDER],
                items=without_passages(transform_sentence_builder(rows)),
                label=part_name,
            )
        )

    # 11-13: true/false, one group per part_name.
    tf_by_part: dict[str, list[dict[str, Any]]] = {}
    for row in load_json("quiz_true_false.json"):
        tf_by_part.setdefault(row["part_name"], []).append(row)
    for part_name, rows in tf_by_part.items():
        groups.append(
            SourceGroup(
                title=f"Sửa lỗi Đúng/Sai - {part_name}",
                description=part_name,
                topic_id=topics[TOPIC_TRUE_FALSE],
                items=without_passages(transform_true_false(rows)),
                label=part_name,
            )
        )

    # 14-16: quiz_with_explanations.json (3 sub-kinds).
    explanations = load_json("quiz_with_explanations.json")
    groups.append(
        SourceGroup(
            title="Ngữ pháp có giải thích",
            description="Rules Quiz with Explanations",
            topic_id=topics[TOPIC_EXPLANATIONS],
            items=without_passages(
                transform_explanations_mcq(explanations["multiple_choice_quizzes"])
            ),
        )
    )
    groups.append(
        SourceGroup(
            title="Lỗi thường gặp",
            description="Common Mistakes in English",
            topic_id=topics[TOPIC_COMMON_ERROR],
            items=without_passages(
                transform_common_error_corrections(
                    explanations["common_error_corrections"], skipped_ids
                )
            ),
        )
    )
    groups.append(
        SourceGroup(
            title="Tìm lỗi sai",
            description="Error Identification Rules",
            topic_id=topics[TOPIC_FIND_ERROR],
            items=without_passages(
                transform_grammar_rules_find_error(explanations["grammar_rules_find_error"])
            ),
        )
    )
    return groups


def _print_summary(rows: list[tuple[str, GroupStats]], layout: PathLayout) -> None:
    print(
        f"\nImport summary (path: {layout.units_per_group} unit(s) x "
        f"{layout.lessons_per_unit} lesson(s) x {layout.challenges_per_lesson} question(s) "
        f"per source group):"
    )
    print(f"  {'group':<46}{'units':>6}{'lessons':>9}{'path Q':>9}{'bank Q':>10}")
    totals = GroupStats()
    for label, stats in rows:
        print(
            f"  {label[:45]:<46}{stats.units:>6}{stats.path_lessons:>9}"
            f"{stats.path_challenges:>9}{stats.bank_challenges:>10}"
        )
        totals.units += stats.units
        totals.path_lessons += stats.path_lessons
        totals.path_challenges += stats.path_challenges
        totals.bank_challenges += stats.bank_challenges
        totals.passages += stats.passages
    print(
        f"  {'TOTAL':<46}{totals.units:>6}{totals.path_lessons:>9}"
        f"{totals.path_challenges:>9}{totals.bank_challenges:>10}"
    )
    print(
        f"\n  {totals.total_challenges} challenges, {totals.passages} passages. "
        f"{totals.path_challenges} of them sit on the walkable path; the rest stay in "
        f"bank lessons, reachable through duo matches and lesson quizzes."
    )


async def main(reset: bool, layout: PathLayout) -> None:
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

        skipped_ids: list[int] = []
        summary: list[tuple[str, GroupStats]] = []
        next_unit_order = 1

        for group in _build_groups(topics, skipped_ids):
            stats = await import_group(
                session,
                course_id,
                group.title,
                group.description,
                group.topic_id,
                group.items,
                layout,
                next_unit_order,
            )
            next_unit_order += stats.units
            label = group.label
            if stats.passages:
                label = f"{label} ({stats.passages} passages)"
            summary.append((label, stats))
            print(f"  imported {label}: {stats.total_challenges} challenges")

        if skipped_ids:
            SKIPPED_LOG_PATH.write_text(json.dumps(skipped_ids, indent=2), encoding="utf-8")

        _print_summary(summary, layout)
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
    parser.add_argument(
        "--challenges-per-lesson",
        type=int,
        default=DEFAULT_CHALLENGES_PER_LESSON,
        help="Questions in one path lesson (default: %(default)s).",
    )
    parser.add_argument(
        "--lessons-per-unit",
        type=int,
        default=DEFAULT_LESSONS_PER_UNIT,
        help="Path lessons in one unit (default: %(default)s).",
    )
    parser.add_argument(
        "--units-per-group",
        type=int,
        default=DEFAULT_UNITS_PER_GROUP,
        help=(
            "Units one source file contributes to the path; everything past that "
            "goes to its bank lesson (default: %(default)s)."
        ),
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            reset=args.reset,
            layout=PathLayout(
                challenges_per_lesson=args.challenges_per_lesson,
                lessons_per_unit=args.lessons_per_unit,
                units_per_group=args.units_per_group,
            ),
        )
    )
