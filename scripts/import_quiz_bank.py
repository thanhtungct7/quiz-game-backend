"""Bulk-import the quiz JSON files under data/quiz/ into a self-contained
"Ngan hang cau hoi" course, laid out as a learning path that climbs the TOEIC
score scale.

Three rules shape the path, in order of importance:

1. One path lesson holds exactly ``--challenges-per-lesson`` questions, all of
   the same question kind and the same source ``toeic_band``.
2. One unit holds ``--lessons-per-unit`` lessons of a single band, cycling
   through every question kind that band has, so two neighbouring lessons are
   never the same kind.
3. Units are emitted band by band, lowest band first -- walking the path is
   walking up the score scale.

The ten raw ``toeic_band`` values are merged into the five bands in `BANDS`:
six of the ten carry only a single question kind, so a unit could not be both
single-band and multi-kind without merging them.

Everything that does not fit on the path lands in a per-band ``is_bank=True``
lesson -- hidden from the course tree, still sampled by duo matches and
practice draws. That split is what keeps a lesson finishable: the bank holds
~144k questions, a lesson holds ten.

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
from collections.abc import Iterable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from random import Random
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
# Roughly Duolingo-sized: 5 bands x 12 units x 20 lessons of ten questions.
DEFAULT_CHALLENGES_PER_LESSON = 10
DEFAULT_LESSONS_PER_UNIT = 20
DEFAULT_MAX_UNITS_PER_BAND = 12
# Fixed so two imports of the same data lay out the same path.
DEFAULT_SEED = 20260831
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

# ---------------------------------------------------------------------------
# Question kinds and TOEIC bands
# ---------------------------------------------------------------------------

KIND_MC4 = "mc4"
KIND_READING = "reading"
KIND_SENTENCE_BUILDER = "sentence_builder"
KIND_TRUE_FALSE = "true_false"
KIND_EXPLANATIONS = "explanations"
KIND_COMMON_ERROR = "common_error"
KIND_FIND_ERROR = "find_error"

TOPIC_OF_KIND = {
    KIND_MC4: "Ngữ pháp - Điền từ",
    KIND_READING: "Đọc hiểu",
    KIND_SENTENCE_BUILDER: "Ghép câu",
    KIND_TRUE_FALSE: "Sửa lỗi Đúng/Sai",
    KIND_EXPLANATIONS: "Ngữ pháp có giải thích",
    KIND_COMMON_ERROR: "Lỗi thường gặp",
    KIND_FIND_ERROR: "Tìm lỗi sai",
}
# Lesson titles: short enough that a path node reads as one glance.
LABEL_OF_KIND = {
    KIND_MC4: "Điền từ",
    KIND_READING: "Đọc hiểu",
    KIND_SENTENCE_BUILDER: "Ghép câu",
    KIND_TRUE_FALSE: "Sửa lỗi Đ/S",
    KIND_EXPLANATIONS: "Ngữ pháp",
    KIND_COMMON_ERROR: "Lỗi thường gặp",
    KIND_FIND_ERROR: "Tìm lỗi sai",
}
ALL_TOPICS = list(TOPIC_OF_KIND.values())


@dataclass(frozen=True)
class Band:
    """One step of the path's score scale.

    `members` maps a question kind to the single raw ``toeic_band`` value that
    kind contributes here -- so a lesson stays pure in the *source* band too,
    not just in the merged one.
    """

    key: str
    label: str
    members: tuple[tuple[str, str], ...]

    @property
    def kinds(self) -> tuple[str, ...]:
        return tuple(kind for kind, _ in self.members)


BANDS: tuple[Band, ...] = (
    Band(
        "B1",
        "250-450",
        (
            (KIND_MC4, "120-350"),
            (KIND_SENTENCE_BUILDER, "250-450"),
            (KIND_TRUE_FALSE, "250-450"),
        ),
    ),
    Band(
        "B2",
        "350-550",
        (
            (KIND_MC4, "350-450"),
            (KIND_READING, "350-550"),
        ),
    ),
    Band(
        "B3",
        "450-700",
        (
            (KIND_MC4, "450-650"),
            (KIND_SENTENCE_BUILDER, "450-650"),
            (KIND_TRUE_FALSE, "450-650"),
            (KIND_COMMON_ERROR, "500-700"),
        ),
    ),
    Band(
        "B4",
        "550-800",
        (
            (KIND_EXPLANATIONS, "550-750"),
            (KIND_MC4, "650-800"),
        ),
    ),
    Band(
        "B5",
        "650-990",
        (
            (KIND_FIND_ERROR, "650-850+"),
            (KIND_READING, "650-850+"),
            (KIND_SENTENCE_BUILDER, "650-850+"),
            (KIND_TRUE_FALSE, "650-850+"),
            (KIND_MC4, "800-990"),
        ),
    ),
)

BAND_INDEX: dict[tuple[str, str], Band] = {
    (kind, raw_band): band for band in BANDS for kind, raw_band in band.members
}

CEFR_ORDER = {"A1": 1, "A2": 2, "B1": 3, "B2": 4, "C1": 5, "C2": 6}


def _cefr_rank(cefr: str) -> tuple[int, int]:
    """Sort key for a `cefr_level` like "A1", "A1-A2" or "B2-C1".

    Decides which kind opens each round of a unit's kind cycle. It cannot
    order questions *within* a kind: the source files carry one constant
    cefr_level per (band, kind), so this is the only ordering it can supply.
    """
    parts = [CEFR_ORDER.get(part.strip(), 0) for part in cefr.split("-") if part.strip()]
    if not parts:
        return (0, 0)
    return (parts[0], parts[-1])


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
    # One dict per article, shared by its 3-4 questions: the raw JSON repeats
    # the whole article on every row, and holding 97k copies of it while the
    # path is being planned is the one thing that would blow this script's
    # memory up.
    passages: dict[str, dict[str, Any]] = {}
    for row in rows:
        options_map: dict[str, str] = row["options_map"]
        correct_key = row["correct_answer"]
        options = [
            {"text": text, "correct": key == correct_key} for key, text in options_map.items()
        ]
        source_ref = row["example_id"]
        passage_info = passages.get(source_ref)
        if passage_info is None:
            passage_info = {
                "source_ref": source_ref,
                "content": row["article"],
                "level_grade": row["level_grade"],
            }
            passages[source_ref] = passage_info
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
# Collecting: source files -> per-band, per-kind pools
# ---------------------------------------------------------------------------

# One imported question: challenge fields, its options in display order, and
# the passage it belongs to (reading comprehension only).
SourceItem = tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any] | None]


def without_passages(
    transform: Iterable[tuple[dict[str, Any], list[dict[str, Any]]]],
) -> Iterator[SourceItem]:
    for challenge_fields, option_dicts in transform:
        yield challenge_fields, option_dicts, None


@dataclass
class KindPool:
    """Every question of one kind inside one band."""

    kind: str
    cefr: str = ""
    items: list[SourceItem] = field(default_factory=list)
    # Filled by _pack_pool: lessons of exactly challenges_per_lesson questions,
    # plus the questions that could not complete one.
    lessons: list[list[SourceItem]] = field(default_factory=list)
    leftover: list[SourceItem] = field(default_factory=list)


BandPools = dict[str, dict[str, KindPool]]


def _collect_pools(skipped_ids: list[int], unbanded: list[tuple[str, SourceItem]]) -> BandPools:
    """Read every source file once, bucketing questions by (band, kind).

    Files are loaded and released one at a time -- the reading corpus alone is
    240 MB of JSON, and holding all five parts at once is the peak this avoids.
    """
    pools: BandPools = {band.key: {} for band in BANDS}

    def add(kind: str, items: Iterator[SourceItem]) -> None:
        for item in items:
            fields = item[0]
            band = BAND_INDEX.get((kind, fields["toeic_band"] or ""))
            if band is None:
                unbanded.append((kind, item))
                continue
            pool = pools[band.key].get(kind)
            if pool is None:
                pool = KindPool(kind=kind, cefr=fields["cefr_level"] or "")
                pools[band.key][kind] = pool
            pool.items.append(item)

    rows = load_json("quiz_multiple_choice_4options.json")
    add(KIND_MC4, without_passages(transform_multiple_choice_4options(rows)))
    del rows

    for part in range(1, 6):
        rows = load_json(f"quiz_reading_comprehension_part{part}.json")
        add(KIND_READING, transform_reading_comprehension(rows))
        del rows

    rows = load_json("quiz_sentence_builder.json")
    add(KIND_SENTENCE_BUILDER, without_passages(transform_sentence_builder(rows)))
    del rows

    rows = load_json("quiz_true_false.json")
    add(KIND_TRUE_FALSE, without_passages(transform_true_false(rows)))
    del rows

    explanations = load_json("quiz_with_explanations.json")
    add(
        KIND_EXPLANATIONS,
        without_passages(transform_explanations_mcq(explanations["multiple_choice_quizzes"])),
    )
    add(
        KIND_COMMON_ERROR,
        without_passages(
            transform_common_error_corrections(
                explanations["common_error_corrections"], skipped_ids
            )
        ),
    )
    add(
        KIND_FIND_ERROR,
        without_passages(
            transform_grammar_rules_find_error(explanations["grammar_rules_find_error"])
        ),
    )
    del explanations

    return pools


# ---------------------------------------------------------------------------
# Packing: pools -> lessons of exactly challenges_per_lesson questions
# ---------------------------------------------------------------------------


def _blocks(pool: KindPool) -> list[list[SourceItem]]:
    """Groups of questions that must not be split across lessons.

    Reading questions come in article-sized blocks of 1-7; every other kind is
    one question per block, which lets one packer serve them all.
    """
    if pool.kind != KIND_READING:
        return [[item] for item in pool.items]
    by_passage: dict[str, list[SourceItem]] = {}
    for item in pool.items:
        passage_info = item[2]
        assert passage_info is not None
        by_passage.setdefault(passage_info["source_ref"], []).append(item)
    return list(by_passage.values())


def _pack_pool(pool: KindPool, per_lesson: int, rng: Random) -> None:
    """Fill `pool.lessons` with lessons of exactly `per_lesson` questions.

    Largest-block-first into the room a lesson has left, so an article is never
    cut in half and a lesson never runs long: 4+3+3, 5+5, 4+4+2, and for the
    single-question kinds simply ten in a row. Whatever cannot complete a
    lesson becomes `pool.leftover` and goes to the band's bank lesson.
    """
    by_size: dict[int, list[list[SourceItem]]] = {}
    for block in _blocks(pool):
        if len(block) > per_lesson:
            pool.leftover.extend(block)
            continue
        by_size.setdefault(len(block), []).append(block)
    for blocks in by_size.values():
        rng.shuffle(blocks)

    while True:
        lesson: list[SourceItem] = []
        remaining = per_lesson
        while remaining > 0:
            size = max((s for s, blocks in by_size.items() if blocks and s <= remaining), default=0)
            if size == 0:
                break
            lesson.extend(by_size[size].pop())
            remaining -= size
        if remaining:
            # Nothing left small enough to close this lesson: the pool is spent.
            pool.leftover.extend(lesson)
            break
        pool.lessons.append(lesson)

    for blocks in by_size.values():
        for block in blocks:
            pool.leftover.extend(block)
    pool.items = []


# ---------------------------------------------------------------------------
# Planning: how many units a band gets, and which kind each lesson is
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PathLayout:
    """The shape of the walkable path."""

    challenges_per_lesson: int = DEFAULT_CHALLENGES_PER_LESSON
    lessons_per_unit: int = DEFAULT_LESSONS_PER_UNIT
    max_units_per_band: int = DEFAULT_MAX_UNITS_PER_BAND


@dataclass(frozen=True)
class BandPlan:
    band: Band
    units: int
    kinds: tuple[str, ...]
    lessons_per_kind: dict[str, int]

    def unit_kind_order(self) -> list[str]:
        """The kind of each lesson in one unit, cycling so neighbours differ."""
        remaining = dict(self.lessons_per_kind)
        order: list[str] = []
        while any(remaining.values()):
            for kind in self.kinds:
                if remaining[kind]:
                    order.append(kind)
                    remaining[kind] -= 1
        return order


def _plan_band(band: Band, pools: dict[str, KindPool], layout: PathLayout) -> BandPlan:
    """Split a unit's lessons between the band's kinds, then see how many such
    units the scarcest kind can actually fill."""
    kinds = tuple(sorted(pools, key=lambda kind: (_cefr_rank(pools[kind].cefr), kind)))
    base, extra = divmod(layout.lessons_per_unit, len(kinds))
    if base == 0:
        raise SystemExit(
            f"Band {band.label} has {len(kinds)} question kinds but --lessons-per-unit is "
            f"{layout.lessons_per_unit}: a unit cannot hold one lesson of each."
        )
    lessons_per_kind = {kind: base + (1 if i < extra else 0) for i, kind in enumerate(kinds)}
    units = min(
        layout.max_units_per_band,
        min(len(pools[kind].lessons) // lessons_per_kind[kind] for kind in kinds),
    )
    if units == 0:
        scarcest = min(kinds, key=lambda kind: len(pools[kind].lessons))
        raise SystemExit(
            f"Band {band.label} cannot fill a single unit: {scarcest} only yields "
            f"{len(pools[scarcest].lessons)} lesson(s), {lessons_per_kind[scarcest]} needed."
        )
    return BandPlan(band=band, units=units, kinds=kinds, lessons_per_kind=lessons_per_kind)


# ---------------------------------------------------------------------------
# Writing
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


@dataclass
class BandStats:
    units: int = 0
    path_lessons: int = 0
    path_challenges: int = 0
    bank_challenges: int = 0
    kinds: int = 0

    @property
    def total_challenges(self) -> int:
        return self.path_challenges + self.bank_challenges


class _ChallengeWriter:
    """Batched writer for challenges, their options and reading passages."""

    def __init__(self, session: AsyncSession, topics: dict[str, str]) -> None:
        self.session = session
        self.topics = topics
        self.batch = Batch()
        self.passage_batch: list[dict[str, Any]] = []
        self.passage_cache: dict[str, str] = {}
        self.passages = 0

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
            self.passages += 1
        return passage_id

    async def add(self, item: SourceItem, kind: str, lesson_id: str, order_index: int) -> None:
        challenge_fields, option_dicts, passage_info = item
        self.batch.add(
            {
                "id": str(uuid4()),
                "lesson_id": lesson_id,
                "topic_id": self.topics[TOPIC_OF_KIND[kind]],
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


async def import_band(
    session: AsyncSession,
    writer: _ChallengeWriter,
    course_id: str,
    plan: BandPlan,
    pools: dict[str, KindPool],
    layout: PathLayout,
    first_unit_order: int,
    extra_bank_items: Sequence[tuple[str, SourceItem]] = (),
) -> BandStats:
    """Write one band: its units, their lessons, and the band's bank lesson."""
    band = plan.band
    stats = BandStats(units=plan.units, kinds=len(plan.kinds))
    cefr_low = min(pools[kind].cefr for kind in plan.kinds)
    cefr_high = max(pools[kind].cefr for kind in plan.kinds)
    cefr_span = cefr_low.split("-")[0]
    if cefr_high.split("-")[-1] != cefr_span:
        cefr_span = f"{cefr_span}-{cefr_high.split('-')[-1]}"
    description = (
        f"TOEIC {band.label} · CEFR {cefr_span} · "
        f"{', '.join(LABEL_OF_KIND[kind] for kind in plan.kinds)}"
    )

    unit_rows: list[dict[str, Any]] = []
    lesson_rows: list[dict[str, Any]] = []
    # (lesson_id, kind) in the order their questions must be written.
    slots: list[tuple[str, str]] = []
    kind_lesson_number = dict.fromkeys(plan.kinds, 0)

    for unit_number in range(1, plan.units + 1):
        unit_id = str(uuid4())
        unit_rows.append(
            {
                "id": unit_id,
                "course_id": course_id,
                "title": f"TOEIC {band.label} · Chặng {unit_number}"[:100],
                "description": description,
                "order_index": first_unit_order + unit_number - 1,
            }
        )
        for order_in_unit, kind in enumerate(plan.unit_kind_order(), start=1):
            lesson_id = str(uuid4())
            kind_lesson_number[kind] += 1
            lesson_rows.append(
                {
                    "id": lesson_id,
                    "unit_id": unit_id,
                    "title": f"{LABEL_OF_KIND[kind]} {kind_lesson_number[kind]}"[:100],
                    "order_index": order_in_unit,
                    "is_bank": False,
                }
            )
            slots.append((lesson_id, kind))
            stats.path_lessons += 1

    # The band's overflow lesson sits in its last unit, past every path lesson.
    bank_lesson_id = str(uuid4())
    lesson_rows.append(
        {
            "id": bank_lesson_id,
            "unit_id": unit_rows[-1]["id"],
            "title": BANK_LESSON_TITLE,
            "order_index": layout.lessons_per_unit + 1,
            "is_bank": True,
        }
    )

    await session.execute(insert(Unit), unit_rows)
    await session.execute(insert(Lesson), lesson_rows)
    await session.commit()

    lesson_cursor = dict.fromkeys(plan.kinds, 0)
    for lesson_id, kind in slots:
        pool = pools[kind]
        for order_index, item in enumerate(pool.lessons[lesson_cursor[kind]], start=1):
            await writer.add(item, kind, lesson_id, order_index)
            stats.path_challenges += 1
        lesson_cursor[kind] += 1

    bank_order = 0
    for kind, pool in pools.items():
        for lesson in pool.lessons[lesson_cursor[kind] :]:
            for item in lesson:
                bank_order += 1
                await writer.add(item, kind, bank_lesson_id, bank_order)
        for item in pool.leftover:
            bank_order += 1
            await writer.add(item, kind, bank_lesson_id, bank_order)
        pool.lessons = []
        pool.leftover = []
    for kind, item in extra_bank_items:
        bank_order += 1
        await writer.add(item, kind, bank_lesson_id, bank_order)
    stats.bank_challenges = bank_order

    await writer.flush()
    return stats


def _print_summary(rows: list[tuple[Band, BandStats]], layout: PathLayout) -> None:
    print(
        f"\nImport summary (path: up to {layout.max_units_per_band} unit(s) x "
        f"{layout.lessons_per_unit} lesson(s) x {layout.challenges_per_lesson} question(s) "
        f"per TOEIC band):"
    )
    print(f"  {'band':<20}{'kinds':>6}{'units':>6}{'lessons':>9}{'path Q':>9}{'bank Q':>10}")
    totals = BandStats()
    for band, stats in rows:
        print(
            f"  {'TOEIC ' + band.label:<20}{stats.kinds:>6}{stats.units:>6}"
            f"{stats.path_lessons:>9}{stats.path_challenges:>9}{stats.bank_challenges:>10}"
        )
        totals.units += stats.units
        totals.path_lessons += stats.path_lessons
        totals.path_challenges += stats.path_challenges
        totals.bank_challenges += stats.bank_challenges
    print(
        f"  {'TOTAL':<20}{'':>6}{totals.units:>6}{totals.path_lessons:>9}"
        f"{totals.path_challenges:>9}{totals.bank_challenges:>10}"
    )
    print(
        f"\n  {totals.total_challenges} challenges. {totals.path_challenges} of them sit on "
        f"the walkable path, lowest band first; the rest stay in bank lessons, reachable "
        f"through duo matches and lesson quizzes."
    )


async def main(reset: bool, layout: PathLayout, seed: int) -> None:
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
        unbanded: list[tuple[str, SourceItem]] = []
        print("Reading source files...")
        pools = _collect_pools(skipped_ids, unbanded)

        rng = Random(seed)  # noqa: S311 -- picking quiz questions, not security
        for band in BANDS:
            for pool in pools[band.key].values():
                _pack_pool(pool, layout.challenges_per_lesson, rng)

        writer = _ChallengeWriter(session, topics)
        summary: list[tuple[Band, BandStats]] = []
        next_unit_order = 1
        for band in BANDS:
            plan = _plan_band(band, pools[band.key], layout)
            # A source band no entry in BANDS covers is still imported: it rides
            # along in the last band's bank lesson rather than being dropped.
            extra = unbanded if band is BANDS[-1] else []
            stats = await import_band(
                session,
                writer,
                course_id,
                plan,
                pools[band.key],
                layout,
                next_unit_order,
                extra,
            )
            next_unit_order += stats.units
            summary.append((band, stats))
            print(
                f"  imported TOEIC {band.label}: {stats.units} units, "
                f"{stats.total_challenges} challenges"
            )

        if unbanded:
            print(f"  {len(unbanded)} question(s) outside every band -> last band's bank lesson")
        await writer.flush()

        if skipped_ids:
            SKIPPED_LOG_PATH.write_text(json.dumps(skipped_ids, indent=2), encoding="utf-8")

        _print_summary(summary, layout)
        print(f"  {writer.passages} reading passages.")
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
        "--max-units-per-band",
        type=int,
        default=DEFAULT_MAX_UNITS_PER_BAND,
        help=(
            "Units one TOEIC band contributes to the path; everything past that "
            "goes to its bank lesson (default: %(default)s)."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Seed for the shuffle that picks questions (default: %(default)s).",
    )
    args = parser.parse_args()
    asyncio.run(
        main(
            reset=args.reset,
            layout=PathLayout(
                challenges_per_lesson=args.challenges_per_lesson,
                lessons_per_unit=args.lessons_per_unit,
                max_units_per_band=args.max_units_per_band,
            ),
            seed=args.seed,
        )
    )
