import pytest

from app.core.exceptions import LessonNotFoundError, UnitNotFoundError
from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.models.content.challenge_option import ChallengeOption
from app.models.content.lesson import Lesson
from app.models.content.unit import Unit
from app.services.content.quiz_service import QuizService


class FakeUnitRepository:
    def __init__(self, units: list[Unit]) -> None:
        self.units = {unit.id: unit for unit in units}

    async def get_by_id(self, unit_id: str) -> Unit | None:
        return self.units.get(unit_id)


class FakeLessonRepository:
    def __init__(self, lessons: list[Lesson]) -> None:
        self.lessons = {lesson.id: lesson for lesson in lessons}

    async def get_by_id(self, lesson_id: str) -> Lesson | None:
        return self.lessons.get(lesson_id)

    async def list_by_unit(self, unit_id: str) -> list[Lesson]:
        return [lesson for lesson in self.lessons.values() if lesson.unit_id == unit_id]


class FakeChallengeRepository:
    def __init__(self, challenges: list[Challenge]) -> None:
        self.challenges = challenges
        self.last_limit: int | None = None

    async def list_by_lesson_filtered(
        self,
        lesson_id: str,
        topic_ids: list[str] | None = None,
        difficulties: list[ChallengeDifficulty] | None = None,
        limit: int | None = None,
    ) -> list[Challenge]:
        pool = [c for c in self.challenges if c.lesson_id == lesson_id]
        if topic_ids:
            pool = [c for c in pool if c.topic_id in topic_ids]
        if difficulties:
            pool = [c for c in pool if c.difficulty in difficulties]
        self.last_limit = limit
        return pool[:limit] if limit is not None else pool

    async def get_by_id(self, challenge_id: str) -> Challenge | None:
        return next((c for c in self.challenges if c.id == challenge_id), None)


def _make_challenge(
    challenge_id: str,
    lesson_id: str,
    *,
    topic_id: str | None = None,
    difficulty: ChallengeDifficulty = ChallengeDifficulty.EASY,
) -> Challenge:
    return Challenge(
        id=challenge_id,
        lesson_id=lesson_id,
        type=ChallengeType.SELECT,
        question=f"Question {challenge_id}",
        difficulty=difficulty,
        topic_id=topic_id,
        order_index=1,
        options=[
            ChallengeOption(id=f"{challenge_id}-a", text="A", correct=True, order_index=1),
            ChallengeOption(id=f"{challenge_id}-b", text="B", correct=False, order_index=2),
            ChallengeOption(id=f"{challenge_id}-c", text="C", correct=False, order_index=3),
        ],
    )


def build_service(
    challenges: list[Challenge], lessons: list[Lesson], units: list[Unit] | None = None
) -> QuizService:
    return QuizService(
        challenges=FakeChallengeRepository(challenges),  # type: ignore[arg-type]
        lessons=FakeLessonRepository(lessons),  # type: ignore[arg-type]
        units=FakeUnitRepository(units or []),  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_generate_for_lesson_requires_existing_lesson() -> None:
    service = build_service([], [])

    with pytest.raises(LessonNotFoundError):
        await service.generate_for_lesson("missing")


@pytest.mark.asyncio
async def test_generate_for_lesson_returns_all_when_pool_smaller_than_count() -> None:
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    challenges = [_make_challenge(f"c{i}", lesson.id) for i in range(3)]
    service = build_service(challenges, [lesson])

    result = await service.generate_for_lesson(lesson.id, count=10)

    assert result.requested_count == 10
    assert result.returned_count == 3
    assert len(result.questions) == 3


@pytest.mark.asyncio
async def test_generate_for_lesson_respects_count_and_has_no_duplicates() -> None:
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    challenges = [_make_challenge(f"c{i}", lesson.id) for i in range(20)]
    service = build_service(challenges, [lesson])

    result = await service.generate_for_lesson(lesson.id, count=5, seed=1)

    assert result.returned_count == 5
    ids = [q.id for q in result.questions]
    assert len(ids) == len(set(ids))


@pytest.mark.asyncio
async def test_generate_for_lesson_filters_by_topic_and_difficulty() -> None:
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    easy, hard = ChallengeDifficulty.EASY, ChallengeDifficulty.HARD
    challenges = [
        _make_challenge("grammar-easy", lesson.id, topic_id="grammar", difficulty=easy),
        _make_challenge("grammar-hard", lesson.id, topic_id="grammar", difficulty=hard),
        _make_challenge("vocab-easy", lesson.id, topic_id="vocab", difficulty=easy),
    ]
    service = build_service(challenges, [lesson])

    result = await service.generate_for_lesson(
        lesson.id,
        topic_ids=["grammar", "vocab"],
        difficulties=[ChallengeDifficulty.EASY],
        count=10,
    )

    ids = {q.id for q in result.questions}
    assert ids == {"grammar-easy", "vocab-easy"}


@pytest.mark.asyncio
async def test_generate_for_lesson_excludes_given_ids() -> None:
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    challenges = [_make_challenge(f"c{i}", lesson.id) for i in range(3)]
    service = build_service(challenges, [lesson])

    result = await service.generate_for_lesson(lesson.id, count=10, exclude_ids=["c0", "c1"])

    ids = {q.id for q in result.questions}
    assert ids == {"c2"}


@pytest.mark.asyncio
async def test_generate_for_lesson_seed_is_reproducible() -> None:
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    challenges = [_make_challenge(f"c{i}", lesson.id) for i in range(20)]
    service = build_service(challenges, [lesson])

    first = await service.generate_for_lesson(lesson.id, count=5, seed=42)
    second = await service.generate_for_lesson(lesson.id, count=5, seed=42)

    assert [q.id for q in first.questions] == [q.id for q in second.questions]
    assert [
        [o.id for o in q.options] for q in first.questions
    ] == [[o.id for o in q.options] for q in second.questions]


@pytest.mark.asyncio
async def test_generate_for_lesson_shuffles_options() -> None:
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    challenge = _make_challenge("c0", lesson.id)
    original_order = [o.id for o in challenge.options]
    service = build_service([challenge], [lesson])

    orders = set()
    for seed in range(20):
        result = await service.generate_for_lesson(lesson.id, count=1, seed=seed)
        orders.add(tuple(o.id for o in result.questions[0].options))

    assert set(original_order) == set(next(iter(orders)))
    assert len(orders) > 1


@pytest.mark.asyncio
async def test_generate_for_unit_requires_existing_unit() -> None:
    service = build_service([], [])

    with pytest.raises(UnitNotFoundError):
        await service.generate_for_unit("missing")


@pytest.mark.asyncio
async def test_generate_for_unit_returns_one_stage_set_per_lesson() -> None:
    unit = Unit(id="unit-1", course_id="course-1", title="Unit 1", description="d", order_index=1)
    lesson_a = Lesson(id="lesson-a", unit_id=unit.id, title="Stage A", order_index=1)
    lesson_b = Lesson(id="lesson-b", unit_id=unit.id, title="Stage B", order_index=2)
    challenges = [
        _make_challenge("a0", lesson_a.id),
        _make_challenge("b0", lesson_b.id),
        _make_challenge("b1", lesson_b.id),
    ]
    service = build_service(challenges, [lesson_a, lesson_b], [unit])

    stage_sets = await service.generate_for_unit(unit.id, count=10)

    by_lesson = {s.lesson_id: s for s in stage_sets}
    assert by_lesson["lesson-a"].lesson_title == "Stage A"
    assert by_lesson["lesson-a"].returned_count == 1
    assert by_lesson["lesson-b"].returned_count == 2
