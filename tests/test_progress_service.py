import pytest

from app.core.exceptions import (
    ChallengeNotFoundError,
    ChallengeOptionNotFoundError,
    LessonNotFoundError,
    UnitNotFoundError,
)
from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.models.content.challenge_option import ChallengeOption
from app.models.content.lesson import Lesson
from app.models.content.unit import Unit
from app.models.progress.user_challenge_progress import UserChallengeProgress
from app.models.progress.user_lesson_progress import LessonProgressStatus, UserLessonProgress
from app.services.progress.progress_service import ProgressService


class FakeChallengeRepository:
    def __init__(self, challenges: list[Challenge]) -> None:
        self.challenges = challenges

    async def get_by_id(self, challenge_id: str) -> Challenge | None:
        return next((c for c in self.challenges if c.id == challenge_id), None)

    async def list_by_lesson(self, lesson_id: str) -> list[Challenge]:
        return [c for c in self.challenges if c.lesson_id == lesson_id]


class FakeLessonRepository:
    def __init__(self, lessons: list[Lesson]) -> None:
        self.lessons = {lesson.id: lesson for lesson in lessons}

    async def get_by_id(self, lesson_id: str) -> Lesson | None:
        return self.lessons.get(lesson_id)

    async def list_by_unit(self, unit_id: str) -> list[Lesson]:
        return sorted(
            (lesson for lesson in self.lessons.values() if lesson.unit_id == unit_id),
            key=lambda lesson: lesson.order_index,
        )


class FakeUnitRepository:
    def __init__(self, units: list[Unit]) -> None:
        self.units = {unit.id: unit for unit in units}

    async def get_by_id(self, unit_id: str) -> Unit | None:
        return self.units.get(unit_id)


class FakeUserProgressRepository:
    """In-memory stand-in that mirrors the real repository's persistence
    semantics closely enough to exercise ProgressService's recompute logic."""

    def __init__(self) -> None:
        self.challenge_progress: dict[tuple[str, str], UserChallengeProgress] = {}
        self.lesson_progress: dict[tuple[str, str], UserLessonProgress] = {}
        self._next_id = 0

    def _new_id(self) -> str:
        self._next_id += 1
        return f"progress-{self._next_id}"

    async def get_challenge_progress(
        self, user_id: str, challenge_id: str
    ) -> UserChallengeProgress | None:
        return self.challenge_progress.get((user_id, challenge_id))

    async def create_challenge_progress(
        self, progress: UserChallengeProgress
    ) -> UserChallengeProgress:
        progress.id = progress.id or self._new_id()
        self.challenge_progress[(progress.user_id, progress.challenge_id)] = progress
        return progress

    async def update_challenge_progress(
        self, progress: UserChallengeProgress, data: dict[str, object]
    ) -> UserChallengeProgress:
        for field, value in data.items():
            setattr(progress, field, value)
        return progress

    async def count_attempted_challenges(self, user_id: str, lesson_id: str) -> int:
        return sum(
            1
            for progress in self.challenge_progress.values()
            if progress.user_id == user_id and progress.lesson_id == lesson_id
        )

    async def count_mastered_challenges(self, user_id: str, lesson_id: str) -> int:
        return sum(
            1
            for progress in self.challenge_progress.values()
            if progress.user_id == user_id
            and progress.lesson_id == lesson_id
            and progress.mastered
        )

    async def get_lesson_progress(
        self, user_id: str, lesson_id: str
    ) -> UserLessonProgress | None:
        return self.lesson_progress.get((user_id, lesson_id))

    async def list_lesson_progress(
        self, user_id: str, lesson_ids: list[str]
    ) -> list[UserLessonProgress]:
        return [
            progress
            for (uid, lesson_id), progress in self.lesson_progress.items()
            if uid == user_id and lesson_id in lesson_ids
        ]

    async def create_lesson_progress(self, progress: UserLessonProgress) -> UserLessonProgress:
        progress.id = progress.id or self._new_id()
        self.lesson_progress[(progress.user_id, progress.lesson_id)] = progress
        return progress

    async def update_lesson_progress(
        self, progress: UserLessonProgress, data: dict[str, object]
    ) -> UserLessonProgress:
        for field, value in data.items():
            setattr(progress, field, value)
        return progress


def _make_challenge(challenge_id: str, lesson_id: str) -> Challenge:
    return Challenge(
        id=challenge_id,
        lesson_id=lesson_id,
        type=ChallengeType.SELECT,
        question=f"Question {challenge_id}",
        difficulty=ChallengeDifficulty.EASY,
        order_index=1,
        options=[
            ChallengeOption(id=f"{challenge_id}-a", text="A", correct=True, order_index=1),
            ChallengeOption(id=f"{challenge_id}-b", text="B", correct=False, order_index=2),
        ],
    )


def build_service(
    challenges: list[Challenge],
    lessons: list[Lesson] | None = None,
    units: list[Unit] | None = None,
) -> tuple[ProgressService, FakeUserProgressRepository]:
    progress_repo = FakeUserProgressRepository()
    service = ProgressService(
        progress=progress_repo,  # type: ignore[arg-type]
        challenges=FakeChallengeRepository(challenges),  # type: ignore[arg-type]
        lessons=FakeLessonRepository(lessons or []),  # type: ignore[arg-type]
        units=FakeUnitRepository(units or []),  # type: ignore[arg-type]
    )
    return service, progress_repo


@pytest.mark.asyncio
async def test_check_answer_requires_existing_challenge() -> None:
    service, _ = build_service([])

    with pytest.raises(ChallengeNotFoundError):
        await service.check_answer("user-1", "missing", "some-option")


@pytest.mark.asyncio
async def test_check_answer_rejects_option_not_on_challenge() -> None:
    challenge = _make_challenge("c0", "lesson-1")
    service, _ = build_service([challenge])

    with pytest.raises(ChallengeOptionNotFoundError):
        await service.check_answer("user-1", "c0", "not-an-option")


@pytest.mark.asyncio
async def test_check_answer_marks_correct_and_incorrect_selections() -> None:
    challenge = _make_challenge("c0", "lesson-1")
    service, _ = build_service([challenge])

    correct = await service.check_answer("user-1", "c0", "c0-a")
    incorrect = await service.check_answer("user-1", "c0", "c0-b")

    assert correct.correct is True
    assert correct.correct_option_ids == ["c0-a"]
    assert incorrect.correct is False


@pytest.mark.asyncio
async def test_check_answer_records_attempt_and_mastery() -> None:
    challenge = _make_challenge("c0", "lesson-1")
    service, progress_repo = build_service([challenge])

    await service.check_answer("user-1", "c0", "c0-b")
    record = await progress_repo.get_challenge_progress("user-1", "c0")
    assert record is not None
    assert record.mastered is False
    assert record.attempts_count == 1
    assert record.mastered_at is None

    await service.check_answer("user-1", "c0", "c0-a")
    record = await progress_repo.get_challenge_progress("user-1", "c0")
    assert record is not None
    assert record.mastered is True
    assert record.attempts_count == 2
    assert record.mastered_at is not None


@pytest.mark.asyncio
async def test_check_answer_keeps_mastery_once_earned() -> None:
    challenge = _make_challenge("c0", "lesson-1")
    service, progress_repo = build_service([challenge])

    await service.check_answer("user-1", "c0", "c0-a")
    first = await progress_repo.get_challenge_progress("user-1", "c0")
    assert first is not None
    first_mastered_at = first.mastered_at

    await service.check_answer("user-1", "c0", "c0-b")
    record = await progress_repo.get_challenge_progress("user-1", "c0")

    assert record is not None
    assert record.mastered is True
    assert record.mastered_at == first_mastered_at
    assert record.attempts_count == 2


@pytest.mark.asyncio
async def test_check_answer_marks_lesson_in_progress_then_completed() -> None:
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    challenge_a = _make_challenge("c0", lesson.id)
    challenge_b = _make_challenge("c1", lesson.id)
    service, progress_repo = build_service([challenge_a, challenge_b], [lesson])

    await service.check_answer("user-1", "c0", "c0-a")
    lesson_progress = await progress_repo.get_lesson_progress("user-1", lesson.id)
    assert lesson_progress is not None
    assert lesson_progress.status == LessonProgressStatus.IN_PROGRESS
    assert lesson_progress.correct_challenge_count == 1
    assert lesson_progress.total_challenge_count == 2
    assert lesson_progress.completed_at is None

    await service.check_answer("user-1", "c1", "c1-a")
    lesson_progress = await progress_repo.get_lesson_progress("user-1", lesson.id)
    assert lesson_progress is not None
    assert lesson_progress.status == LessonProgressStatus.COMPLETED
    assert lesson_progress.correct_challenge_count == 2
    assert lesson_progress.completed_at is not None


@pytest.mark.asyncio
async def test_get_lesson_progress_defaults_to_not_started() -> None:
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    challenge = _make_challenge("c0", lesson.id)
    service, _ = build_service([challenge], [lesson])

    result = await service.get_lesson_progress("user-1", lesson.id)

    assert result.status == LessonProgressStatus.NOT_STARTED
    assert result.correct_challenge_count == 0
    assert result.total_challenge_count == 1


@pytest.mark.asyncio
async def test_get_lesson_progress_requires_existing_lesson() -> None:
    service, _ = build_service([])

    with pytest.raises(LessonNotFoundError):
        await service.get_lesson_progress("user-1", "missing")


@pytest.mark.asyncio
async def test_get_unit_progress_mixes_started_and_untouched_lessons() -> None:
    unit = Unit(id="unit-1", course_id="course-1", title="Unit 1", description="d", order_index=1)
    lesson_a = Lesson(id="lesson-a", unit_id=unit.id, title="A", order_index=1)
    lesson_b = Lesson(id="lesson-b", unit_id=unit.id, title="B", order_index=2)
    challenge_a = _make_challenge("ca", lesson_a.id)
    challenge_b = _make_challenge("cb", lesson_b.id)
    service, _ = build_service([challenge_a, challenge_b], [lesson_a, lesson_b], [unit])

    await service.check_answer("user-1", "ca", "ca-a")
    result = await service.get_unit_progress("user-1", unit.id)

    by_lesson = {row.lesson_id: row for row in result.lessons}
    assert by_lesson["lesson-a"].status == LessonProgressStatus.COMPLETED
    assert by_lesson["lesson-b"].status == LessonProgressStatus.NOT_STARTED


@pytest.mark.asyncio
async def test_get_unit_progress_requires_existing_unit() -> None:
    service, _ = build_service([])

    with pytest.raises(UnitNotFoundError):
        await service.get_unit_progress("user-1", "missing")
