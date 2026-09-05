import pytest

from app.core.exceptions import (
    ChallengeNotFoundError,
    ChallengeOptionNotFoundError,
    CourseNotFoundError,
    InvalidAnswerSubmissionError,
    LessonNotFoundError,
    UnitNotFoundError,
)
from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.models.content.challenge_option import ChallengeOption
from app.models.content.course import Course
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

    async def count_by_lesson(self, lesson_id: str) -> int:
        return sum(1 for c in self.challenges if c.lesson_id == lesson_id)

    async def count_by_lessons(self, lesson_ids: list[str]) -> dict[str, int]:
        counts: dict[str, int] = {}
        for challenge in self.challenges:
            if challenge.lesson_id in lesson_ids:
                counts[challenge.lesson_id] = counts.get(challenge.lesson_id, 0) + 1
        return counts


class FakeLessonRepository:
    def __init__(self, lessons: list[Lesson]) -> None:
        self.lessons = {lesson.id: lesson for lesson in lessons}

    async def get_by_id(self, lesson_id: str) -> Lesson | None:
        return self.lessons.get(lesson_id)

    async def list_by_unit(self, unit_id: str, *, include_bank: bool = False) -> list[Lesson]:
        return sorted(
            (
                lesson
                for lesson in self.lessons.values()
                if lesson.unit_id == unit_id and (include_bank or not lesson.is_bank)
            ),
            key=lambda lesson: lesson.order_index,
        )

    async def list_path_by_course(self, course_id: str) -> list[Lesson]:
        return sorted(
            (
                lesson
                for lesson in self.lessons.values()
                if lesson.unit.course_id == course_id and not lesson.is_bank
            ),
            key=lambda lesson: (lesson.unit.order_index, lesson.order_index),
        )


class FakeUnitRepository:
    def __init__(self, units: list[Unit]) -> None:
        self.units = {unit.id: unit for unit in units}

    async def get_by_id(self, unit_id: str) -> Unit | None:
        return self.units.get(unit_id)


class FakeCourseRepository:
    def __init__(self, courses: list[Course]) -> None:
        self.courses = {course.id: course for course in courses}

    async def get_by_id(self, course_id: str) -> Course | None:
        return self.courses.get(course_id)


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


def _make_order_challenge(challenge_id: str, lesson_id: str, words: list[str]) -> Challenge:
    """A word-ordering challenge whose answer is the option order_index run.

    Options are built in solution order and every one is flagged correct --
    that is exactly what the importer writes for a "ghép câu" question.
    """
    return Challenge(
        id=challenge_id,
        lesson_id=lesson_id,
        type=ChallengeType.ORDER,
        question=f"Question {challenge_id}",
        difficulty=ChallengeDifficulty.EASY,
        order_index=1,
        options=[
            ChallengeOption(
                id=f"{challenge_id}-{position}", text=word, correct=True, order_index=position
            )
            for position, word in enumerate(words, start=1)
        ],
    )


def build_service(
    challenges: list[Challenge],
    lessons: list[Lesson] | None = None,
    units: list[Unit] | None = None,
    courses: list[Course] | None = None,
) -> tuple[ProgressService, FakeUserProgressRepository]:
    progress_repo = FakeUserProgressRepository()
    service = ProgressService(
        progress=progress_repo,  # type: ignore[arg-type]
        challenges=FakeChallengeRepository(challenges),  # type: ignore[arg-type]
        lessons=FakeLessonRepository(lessons or []),  # type: ignore[arg-type]
        units=FakeUnitRepository(units or []),  # type: ignore[arg-type]
        courses=FakeCourseRepository(courses or []),  # type: ignore[arg-type]
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
async def test_marking_a_lesson_completed_does_not_need_every_challenge_mastered() -> None:
    """What the battle gate needs.

    A fight ends when the monster falls, not when the pool has been mastered,
    so completion has to be something the caller can state outright -- while the
    mastered count keeps telling the truth about how much was actually learned.
    """
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    challenge_a = _make_challenge("c0", lesson.id)
    challenge_b = _make_challenge("c1", lesson.id)
    service, progress_repo = build_service([challenge_a, challenge_b], [lesson])

    await service.check_answer("user-1", "c0", "c0-a")
    await service.mark_lesson_completed("user-1", lesson.id)

    stored = await progress_repo.get_lesson_progress("user-1", lesson.id)
    assert stored is not None
    assert stored.status == LessonProgressStatus.COMPLETED
    assert stored.completed_at is not None
    assert stored.correct_challenge_count == 1
    assert stored.total_challenge_count == 2


@pytest.mark.asyncio
async def test_a_completed_lesson_is_never_walked_back_by_a_later_answer() -> None:
    """Completion is a ratchet.

    Replaying a cleared gate answers questions again, and recomputing the status
    from mastery alone would drop a 1-of-2 lesson back to IN_PROGRESS -- which on
    the path means the next lesson silently re-locks.
    """
    lesson = Lesson(id="lesson-1", unit_id="unit-1", title="Lesson 1", order_index=1)
    challenge_a = _make_challenge("c0", lesson.id)
    challenge_b = _make_challenge("c1", lesson.id)
    service, progress_repo = build_service([challenge_a, challenge_b], [lesson])

    await service.mark_lesson_completed("user-1", lesson.id)
    completed_at = (await progress_repo.get_lesson_progress("user-1", lesson.id)).completed_at

    await service.check_answer("user-1", "c0", "c0-b")

    stored = await progress_repo.get_lesson_progress("user-1", lesson.id)
    assert stored is not None
    assert stored.status == LessonProgressStatus.COMPLETED
    # Set once and never moved, so a replay cannot pay the completion twice.
    assert stored.completed_at == completed_at


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


@pytest.mark.asyncio
async def test_get_unit_progress_skips_bank_lessons() -> None:
    unit = Unit(id="unit-1", course_id="course-1", title="Unit 1", description="d", order_index=1)
    path = Lesson(id="lesson-a", unit_id=unit.id, title="Cửa 1", order_index=1, is_bank=False)
    bank = Lesson(id="lesson-bank", unit_id=unit.id, title="Bank", order_index=21, is_bank=True)
    service, _ = build_service(
        [_make_challenge("ca", path.id), _make_challenge("cb", bank.id)],
        [path, bank],
        [unit],
    )

    result = await service.get_unit_progress("user-1", unit.id)

    assert [row.lesson_id for row in result.lessons] == [path.id]


@pytest.mark.asyncio
async def test_get_course_progress_covers_every_path_lesson_in_one_call() -> None:
    course = Course(id="course-1", title="English", image_src="/en.svg")
    unit_one = Unit(
        id="unit-1", course_id=course.id, title="Unit 1", description="d", order_index=1
    )
    unit_two = Unit(
        id="unit-2", course_id=course.id, title="Unit 2", description="d", order_index=2
    )
    lesson_a = Lesson(id="lesson-a", unit_id=unit_one.id, title="A", order_index=1)
    lesson_b = Lesson(id="lesson-b", unit_id=unit_two.id, title="B", order_index=1)
    bank = Lesson(id="lesson-bank", unit_id=unit_two.id, title="Bank", order_index=21, is_bank=True)
    lesson_a.unit, lesson_b.unit, bank.unit = unit_one, unit_two, unit_two
    service, _ = build_service(
        [_make_challenge("ca", lesson_a.id), _make_challenge("cb", lesson_b.id)],
        [lesson_a, lesson_b, bank],
        [unit_one, unit_two],
        [course],
    )

    await service.check_answer("user-1", "ca", "ca-a")
    result = await service.get_course_progress("user-1", course.id)

    assert result.course_id == course.id
    # Course-tree order, bank lesson left out.
    assert [row.lesson_id for row in result.lessons] == [lesson_a.id, lesson_b.id]
    assert result.lessons[0].status == LessonProgressStatus.COMPLETED
    assert result.lessons[1].status == LessonProgressStatus.NOT_STARTED
    assert result.lessons[1].total_challenge_count == 1


@pytest.mark.asyncio
async def test_get_course_progress_requires_existing_course() -> None:
    service, _ = build_service([])

    with pytest.raises(CourseNotFoundError):
        await service.get_course_progress("user-1", "missing")


@pytest.mark.asyncio
async def test_check_answer_accepts_the_right_word_order() -> None:
    challenge = _make_order_challenge("c0", "lesson-1", ["Tôi", "phải", "đi", "ngủ"])
    service, _ = build_service([challenge])

    result = await service.check_answer(
        "user-1", "c0", selected_option_ids=["c0-1", "c0-2", "c0-3", "c0-4"]
    )

    assert result.correct is True
    assert result.correct_option_ids == ["c0-1", "c0-2", "c0-3", "c0-4"]


@pytest.mark.asyncio
async def test_check_answer_rejects_the_wrong_word_order() -> None:
    """The bug this type used to have: every tile is flagged correct, so
    grading on the flags made any arrangement -- and any single tap -- pass."""
    challenge = _make_order_challenge("c0", "lesson-1", ["Tôi", "phải", "đi", "ngủ"])
    service, _ = build_service([challenge])

    result = await service.check_answer(
        "user-1", "c0", selected_option_ids=["c0-2", "c0-1", "c0-4", "c0-3"]
    )

    assert result.correct is False
    assert result.correct_option_ids == ["c0-1", "c0-2", "c0-3", "c0-4"]


@pytest.mark.asyncio
async def test_check_answer_rejects_a_partial_word_order() -> None:
    challenge = _make_order_challenge("c0", "lesson-1", ["Tôi", "phải", "đi", "ngủ"])
    service, _ = build_service([challenge])

    result = await service.check_answer("user-1", "c0", selected_option_ids=["c0-1", "c0-2"])

    assert result.correct is False


@pytest.mark.asyncio
async def test_check_answer_treats_repeated_words_as_interchangeable() -> None:
    """Two tiles carrying the same word spell the same sentence either way
    round, so grading compares words rather than option ids."""
    challenge = _make_order_challenge("c0", "lesson-1", ["càng", "học", "càng", "giỏi"])
    service, _ = build_service([challenge])

    result = await service.check_answer(
        "user-1", "c0", selected_option_ids=["c0-3", "c0-2", "c0-1", "c0-4"]
    )

    assert result.correct is True


@pytest.mark.asyncio
async def test_check_answer_rejects_a_reused_tile() -> None:
    challenge = _make_order_challenge("c0", "lesson-1", ["Tôi", "phải", "đi", "ngủ"])
    service, _ = build_service([challenge])

    with pytest.raises(InvalidAnswerSubmissionError):
        await service.check_answer(
            "user-1", "c0", selected_option_ids=["c0-1", "c0-1", "c0-1", "c0-1"]
        )


@pytest.mark.asyncio
async def test_check_answer_rejects_a_single_tap_on_an_order_challenge() -> None:
    challenge = _make_order_challenge("c0", "lesson-1", ["Tôi", "phải", "đi", "ngủ"])
    service, _ = build_service([challenge])

    with pytest.raises(InvalidAnswerSubmissionError):
        await service.check_answer("user-1", "c0", "c0-1")


@pytest.mark.asyncio
async def test_check_answer_rejects_a_sequence_on_a_single_choice_challenge() -> None:
    challenge = _make_challenge("c0", "lesson-1")
    service, _ = build_service([challenge])

    with pytest.raises(InvalidAnswerSubmissionError):
        await service.check_answer("user-1", "c0", selected_option_ids=["c0-a"])


@pytest.mark.asyncio
async def test_check_answer_records_mastery_for_an_order_challenge() -> None:
    """An ORDER answer has no single selected option, but it still counts as
    an attempt and still masters the challenge."""
    challenge = _make_order_challenge("c0", "lesson-1", ["Tôi", "phải", "đi", "ngủ"])
    service, progress_repo = build_service([challenge])

    await service.check_answer("user-1", "c0", selected_option_ids=["c0-2", "c0-1"])
    record = await progress_repo.get_challenge_progress("user-1", "c0")
    assert record is not None
    assert record.mastered is False
    assert record.last_selected_option_id is None

    await service.check_answer(
        "user-1", "c0", selected_option_ids=["c0-1", "c0-2", "c0-3", "c0-4"]
    )
    record = await progress_repo.get_challenge_progress("user-1", "c0")
    assert record is not None
    assert record.mastered is True
    assert record.attempts_count == 2
