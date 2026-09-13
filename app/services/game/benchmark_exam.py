"""The rules of the Benchmark Exam, as pure functions.

Everything that decides whether a learner is let through a chốt chặn năng lực
-- how long the paper is, what counts as a pass, which units it is drawn from,
how long a sitting stays open -- lives here with no I/O, so it can be pinned
down by tests without a database. `BenchmarkExamService` is the part that
reads and writes.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models.content.lesson import Lesson

# How long the paper is. The draw can come up short if the course holds less
# content than this; a sitting is graded against what was actually drawn.
QUESTION_COUNT = 30
# Below this there is not enough of an exam to stand between a learner and a
# CEFR band, so no sitting is opened at all.
MIN_QUESTION_COUNT = 10
PASS_PERCENT = 80
# Three units span a band without the draw costing more queries than the exam
# is worth.
MAX_SOURCE_UNITS = 3
# Two per lesson keeps the pool comfortably above QUESTION_COUNT even when only
# a single unit qualifies.
QUESTIONS_PER_LESSON = 2
TIME_LIMIT = timedelta(minutes=45)
# An answer sent in the last second of the clock should not be lost to the
# round trip. Only answers get this; the clock the client shows is TIME_LIMIT.
EXPIRY_GRACE = timedelta(seconds=30)


@dataclass(frozen=True)
class PathUnit:
    """One unit of the learn path, as the lessons that make it up."""

    unit_id: str
    lesson_ids: tuple[str, ...]


def group_path(lessons: Sequence[Lesson]) -> list[PathUnit]:
    """Fold a course's path, already in path order, into its units."""
    units: dict[str, list[str]] = {}
    for lesson in lessons:
        units.setdefault(lesson.unit_id, []).append(lesson.id)
    return [PathUnit(unit_id, tuple(ids)) for unit_id, ids in units.items()]


def pick_source_units(
    units: Sequence[PathUnit], completed_lesson_ids: set[str]
) -> list[PathUnit]:
    """Which units the paper is drawn from: what the learner has actually
    finished, sampled across the whole of it rather than taken off the end.

    An exam over one unit would be an exam over one topic, so up to
    MAX_SOURCE_UNITS are spread evenly over the finished ones -- first, middle
    and last. A learner who reached a cap without finishing a unit outright
    falls back to units they have at least started, and then to the opening
    unit, because an exam with no questions is worse than an easy one.
    """
    finished = [
        unit
        for unit in units
        if unit.lesson_ids and all(lesson in completed_lesson_ids for lesson in unit.lesson_ids)
    ]
    started = [
        unit for unit in units if any(lesson in completed_lesson_ids for lesson in unit.lesson_ids)
    ]
    candidates = finished or started or list(units[:1])
    return spread(candidates, MAX_SOURCE_UNITS)


def spread[T](items: Sequence[T], limit: int) -> list[T]:
    """Up to `limit` entries taken evenly across `items`, endpoints included."""
    if len(items) <= limit:
        return list(items)
    if limit <= 1:
        return list(items[:limit])
    picked: list[T] = []
    for step in range(limit):
        item = items[step * (len(items) - 1) // (limit - 1)]
        if item not in picked:
            picked.append(item)
    return picked


def percent(correct: int, total: int) -> int:
    return 0 if total <= 0 else correct * 100 // total


def is_passing(correct: int, total: int, pass_percent: int) -> bool:
    """An empty paper is never a pass."""
    return total > 0 and correct * 100 >= total * pass_percent


def accepts_answers_at(expires_at: datetime, now: datetime) -> bool:
    return now <= expires_at + EXPIRY_GRACE
