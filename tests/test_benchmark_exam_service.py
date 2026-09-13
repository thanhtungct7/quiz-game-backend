from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.core.exceptions import (
    BenchmarkExamAlreadyClearedError,
    BenchmarkExamAttemptClosedError,
    BenchmarkExamAttemptNotFoundError,
    BenchmarkExamNotEligibleError,
    BenchmarkExamNotEnoughQuestionsError,
    BenchmarkExamQuestionAlreadyAnsweredError,
    BenchmarkExamQuestionNotInAttemptError,
)
from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.models.content.challenge_option import ChallengeOption
from app.models.content.course import Course
from app.models.content.lesson import Lesson
from app.models.game.benchmark_exam_attempt import BenchmarkAttemptStatus, BenchmarkExamAttempt
from app.models.game.user_game_profile import UserGameProfile
from app.models.progress.user_lesson_progress import LessonProgressStatus, UserLessonProgress
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.game.game import EnergyRead, GameProfileRead
from app.services.content.challenge_presenter import to_public_challenge
from app.services.game.benchmark_exam import EXPIRY_GRACE, PASS_PERCENT, TIME_LIMIT
from app.services.game.benchmark_exam_service import BenchmarkExamService
from app.services.game.cefr import LEVEL_CAPS
from app.services.game.leveling import exp_for_level

CAP = LEVEL_CAPS[0]
NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


def _select(challenge_id: str, lesson_id: str) -> Challenge:
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


def _order(challenge_id: str, lesson_id: str, words: list[str]) -> Challenge:
    return Challenge(
        id=challenge_id,
        lesson_id=lesson_id,
        type=ChallengeType.ORDER,
        question=f"Question {challenge_id}",
        difficulty=ChallengeDifficulty.EASY,
        order_index=1,
        options=[
            ChallengeOption(id=f"{challenge_id}-{i}", text=word, correct=True, order_index=i)
            for i, word in enumerate(words, start=1)
        ],
    )


class FakeAttempts:
    def __init__(self) -> None:
        self.rows: dict[str, BenchmarkExamAttempt] = {}

    async def create(self, attempt: BenchmarkExamAttempt) -> BenchmarkExamAttempt:
        attempt.id = attempt.id or str(uuid4())
        self.rows[attempt.id] = attempt
        return attempt

    async def get_for_update(self, user_id: str, attempt_id: str) -> BenchmarkExamAttempt | None:
        row = self.rows.get(attempt_id)
        return row if row is not None and row.user_id == user_id else None

    async def get_in_progress(self, user_id: str) -> BenchmarkExamAttempt | None:
        return next(
            (
                row
                for row in self.rows.values()
                if row.user_id == user_id and row.status is BenchmarkAttemptStatus.IN_PROGRESS
            ),
            None,
        )

    async def save(
        self, attempt: BenchmarkExamAttempt, data: dict[str, object]
    ) -> BenchmarkExamAttempt:
        for field, value in data.items():
            setattr(attempt, field, value)
        return attempt

    async def list_for_user(self, user_id: str, limit: int) -> list[BenchmarkExamAttempt]:
        rows = [row for row in self.rows.values() if row.user_id == user_id]
        return sorted(rows, key=lambda row: row.started_at, reverse=True)[:limit]


class FakeProfiles:
    def __init__(self, profile: UserGameProfile) -> None:
        self.profile = profile

    async def get_or_create(self, user_id: str) -> UserGameProfile:
        return self.profile


class FakeGame:
    def __init__(self, profiles: FakeProfiles) -> None:
        self.profiles = profiles
        self.cleared: list[int] = []

    async def clear_benchmark_cap(self, user_id: str, cap_level: int) -> GameProfileRead:
        self.cleared.append(cap_level)
        profile = self.profiles.profile
        profile.benchmark_cleared_level = max(profile.benchmark_cleared_level, cap_level)
        return await self.get_profile(user_id)

    async def get_profile(self, user_id: str) -> GameProfileRead:
        return GameProfileRead(
            user_id=user_id,
            level=1,
            total_exp=self.profiles.profile.total_exp,
            exp_for_current_level=0,
            exp_for_next_level=0,
            exp_to_next_level=0,
            gold=0,
            class_code=None,
            class_name=None,
            energy=EnergyRead(current=5, maximum=5, next_regen_at=None),
            day_streak=0,
            best_day_streak=0,
            pending_benchmark_level=None,
        )


class FakeQuiz:
    def __init__(self, challenges: list[Challenge]) -> None:
        self.challenges = challenges
        self.drawn_from: list[str] = []

    async def draw_for_benchmark(
        self, lesson_ids: list[str], per_lesson: int, count: int, seed: int | None = None
    ) -> list[ChallengePublicRead]:
        self.drawn_from = list(lesson_ids)
        return [
            to_public_challenge(challenge)
            for challenge in self.challenges
            if challenge.lesson_id in lesson_ids
        ][:count]


class FakeChallenges:
    def __init__(self, challenges: list[Challenge]) -> None:
        self.by_id = {challenge.id: challenge for challenge in challenges}

    async def get_by_id(self, challenge_id: str) -> Challenge | None:
        return self.by_id.get(challenge_id)


class FakeCourses:
    def __init__(self, courses: list[Course]) -> None:
        self.courses = courses

    async def list_all(self) -> list[Course]:
        return list(self.courses)


class FakeLessons:
    def __init__(self, paths: dict[str, list[Lesson]]) -> None:
        self.paths = paths

    async def list_path_by_course(self, course_id: str) -> list[Lesson]:
        return list(self.paths.get(course_id, []))


class FakeProgress:
    def __init__(self, completed: set[str]) -> None:
        self.completed = completed

    async def list_lesson_progress(
        self, user_id: str, lesson_ids: list[str]
    ) -> list[UserLessonProgress]:
        return [
            UserLessonProgress(
                user_id=user_id, lesson_id=lesson_id, status=LessonProgressStatus.COMPLETED
            )
            for lesson_id in lesson_ids
            if lesson_id in self.completed
        ]


class Harness:
    """One course of one unit holding `question_count` single-choice questions."""

    def __init__(
        self,
        *,
        total_exp: int | None = None,
        cleared: int = 0,
        question_count: int = 30,
        extra: list[Challenge] | None = None,
    ) -> None:
        self.lessons = [
            Lesson(id=f"l{i}", unit_id="u1", title=f"L{i}", order_index=i, is_bank=False)
            for i in range(3)
        ]
        self.challenges = [
            _select(f"c{i}", self.lessons[i % 3].id) for i in range(question_count)
        ] + (extra or [])
        self.profiles = FakeProfiles(
            UserGameProfile(
                user_id="user-1",
                total_exp=exp_for_level(CAP + 5) if total_exp is None else total_exp,
                benchmark_cleared_level=cleared,
            )
        )
        self.attempts = FakeAttempts()
        self.game = FakeGame(self.profiles)
        self.quiz = FakeQuiz(self.challenges)
        self.service = BenchmarkExamService(
            attempts=self.attempts,  # type: ignore[arg-type]
            profiles=self.profiles,  # type: ignore[arg-type]
            game=self.game,  # type: ignore[arg-type]
            quiz=self.quiz,  # type: ignore[arg-type]
            challenges=FakeChallenges(self.challenges),  # type: ignore[arg-type]
            courses=FakeCourses([Course(id="course-1", title="C", image_src="")]),  # type: ignore[arg-type]
            lessons=FakeLessons({"course-1": self.lessons}),  # type: ignore[arg-type]
            progress=FakeProgress({lesson.id for lesson in self.lessons}),  # type: ignore[arg-type]
        )

    async def answer_all(self, attempt_id: str, question_ids: list[str], right: int) -> None:
        for index, challenge_id in enumerate(question_ids):
            option = "a" if index < right else "b"
            await self.service.answer(
                "user-1", attempt_id, challenge_id, f"{challenge_id}-{option}", now=NOW
            )


# --- starting ------------------------------------------------------------------


async def test_a_sitting_opens_on_a_server_drawn_paper() -> None:
    harness = Harness()

    paper = await harness.service.start("user-1", CAP, now=NOW)

    assert paper.total == 30
    assert paper.pass_percent == PASS_PERCENT
    assert paper.expires_at == NOW + TIME_LIMIT
    stored = harness.attempts.rows[paper.attempt_id]
    assert stored.question_ids == [question.id for question in paper.questions]
    assert stored.status is BenchmarkAttemptStatus.IN_PROGRESS


async def test_the_paper_never_carries_the_answers() -> None:
    harness = Harness()

    paper = await harness.service.start("user-1", CAP, now=NOW)

    dumped = paper.model_dump()
    for question in dumped["questions"]:
        for option in question["options"]:
            assert "correct" not in option


async def test_an_unknown_cap_cannot_be_sat() -> None:
    with pytest.raises(BenchmarkExamNotEligibleError):
        await Harness().service.start("user-1", CAP + 1, now=NOW)


async def test_a_cap_cannot_be_sat_before_the_level_is_reached() -> None:
    harness = Harness(total_exp=exp_for_level(CAP - 1))

    with pytest.raises(BenchmarkExamNotEligibleError):
        await harness.service.start("user-1", CAP, now=NOW)


async def test_a_cleared_cap_cannot_be_sat_again() -> None:
    harness = Harness(cleared=CAP)

    with pytest.raises(BenchmarkExamAlreadyClearedError):
        await harness.service.start("user-1", CAP, now=NOW)


async def test_too_little_content_opens_no_sitting() -> None:
    harness = Harness(question_count=9)

    with pytest.raises(BenchmarkExamNotEnoughQuestionsError):
        await harness.service.start("user-1", CAP, now=NOW)
    assert harness.attempts.rows == {}


async def test_starting_again_abandons_the_sitting_still_running() -> None:
    harness = Harness()
    first = await harness.service.start("user-1", CAP, now=NOW)

    second = await harness.service.start("user-1", CAP, now=NOW + timedelta(minutes=5))

    assert harness.attempts.rows[first.attempt_id].status is BenchmarkAttemptStatus.ABANDONED
    assert harness.attempts.rows[second.attempt_id].status is BenchmarkAttemptStatus.IN_PROGRESS


async def test_starting_again_grades_a_sitting_whose_time_ran_out() -> None:
    harness = Harness()
    first = await harness.service.start("user-1", CAP, now=NOW)
    await harness.answer_all(
        first.attempt_id, harness.attempts.rows[first.attempt_id].question_ids, 30
    )

    later = NOW + TIME_LIMIT + EXPIRY_GRACE + timedelta(minutes=1)
    # The expired sitting was a pass, so the cap it lifts is already behind the player.
    with pytest.raises(BenchmarkExamAlreadyClearedError):
        await harness.service.start("user-1", CAP, now=later)

    assert harness.attempts.rows[first.attempt_id].status is BenchmarkAttemptStatus.PASSED
    assert harness.game.cleared == [CAP]
    assert len(harness.attempts.rows) == 1


# --- answering -----------------------------------------------------------------


async def test_an_answer_is_taken_without_saying_whether_it_was_right() -> None:
    harness = Harness()
    paper = await harness.service.start("user-1", CAP, now=NOW)
    question = paper.questions[0].id

    ack = await harness.service.answer(
        "user-1", paper.attempt_id, question, f"{question}-b", now=NOW
    )

    assert set(ack.model_dump()) == {"attempt_id", "answered_count", "total"}
    assert ack.answered_count == 1
    assert harness.attempts.rows[paper.attempt_id].answers[question]["correct"] is False


async def test_a_question_is_answered_once() -> None:
    harness = Harness()
    paper = await harness.service.start("user-1", CAP, now=NOW)
    question = paper.questions[0].id
    await harness.service.answer("user-1", paper.attempt_id, question, f"{question}-b", now=NOW)

    with pytest.raises(BenchmarkExamQuestionAlreadyAnsweredError):
        await harness.service.answer("user-1", paper.attempt_id, question, f"{question}-a", now=NOW)


async def test_a_question_not_on_the_paper_is_refused() -> None:
    harness = Harness(extra=[_select("elsewhere", "bank")])
    paper = await harness.service.start("user-1", CAP, now=NOW)

    with pytest.raises(BenchmarkExamQuestionNotInAttemptError):
        await harness.service.answer(
            "user-1", paper.attempt_id, "elsewhere", "elsewhere-a", now=NOW
        )


async def test_another_players_sitting_does_not_exist() -> None:
    harness = Harness()
    paper = await harness.service.start("user-1", CAP, now=NOW)
    question = paper.questions[0].id

    with pytest.raises(BenchmarkExamAttemptNotFoundError):
        await harness.service.answer("user-2", paper.attempt_id, question, f"{question}-a", now=NOW)


async def test_an_answer_in_the_grace_period_still_counts() -> None:
    harness = Harness()
    paper = await harness.service.start("user-1", CAP, now=NOW)
    question = paper.questions[0].id

    ack = await harness.service.answer(
        "user-1", paper.attempt_id, question, f"{question}-a", now=NOW + TIME_LIMIT + EXPIRY_GRACE
    )

    assert ack.answered_count == 1


async def test_an_answer_after_time_is_up_closes_and_grades_the_sitting() -> None:
    harness = Harness()
    paper = await harness.service.start("user-1", CAP, now=NOW)
    question = paper.questions[0].id
    late = NOW + TIME_LIMIT + EXPIRY_GRACE + timedelta(seconds=1)

    with pytest.raises(BenchmarkExamAttemptClosedError):
        await harness.service.answer(
            "user-1", paper.attempt_id, question, f"{question}-a", now=late
        )

    stored = harness.attempts.rows[paper.attempt_id]
    assert stored.status is BenchmarkAttemptStatus.FAILED
    assert stored.correct_count == 0


async def test_word_order_is_graded_by_the_same_rules_as_study() -> None:
    # Two tiles carrying the same word are interchangeable, exactly as in a lesson.
    repeated = _order("order-1", "l0", ["càng", "học", "càng", "giỏi"])
    harness = Harness(question_count=12, extra=[repeated])
    paper = await harness.service.start("user-1", CAP, now=NOW)

    await harness.service.answer(
        "user-1",
        paper.attempt_id,
        "order-1",
        selected_option_ids=["order-1-3", "order-1-2", "order-1-1", "order-1-4"],
        now=NOW,
    )

    assert harness.attempts.rows[paper.attempt_id].answers["order-1"]["correct"] is True


# --- handing in ------------------------------------------------------------------


async def test_eighty_percent_passes_and_lifts_the_cap() -> None:
    harness = Harness()
    paper = await harness.service.start("user-1", CAP, now=NOW)
    await harness.answer_all(paper.attempt_id, [q.id for q in paper.questions], right=24)

    result = await harness.service.submit("user-1", paper.attempt_id, now=NOW)

    assert result.passed
    assert (result.correct_count, result.total, result.percent) == (24, 30, 80)
    assert harness.game.cleared == [CAP]


async def test_one_short_of_the_bar_fails_and_lifts_nothing() -> None:
    harness = Harness()
    paper = await harness.service.start("user-1", CAP, now=NOW)
    await harness.answer_all(paper.attempt_id, [q.id for q in paper.questions], right=23)

    result = await harness.service.submit("user-1", paper.attempt_id, now=NOW)

    assert not result.passed
    assert result.status is BenchmarkAttemptStatus.FAILED
    assert harness.game.cleared == []


async def test_unanswered_questions_count_as_wrong() -> None:
    harness = Harness()
    paper = await harness.service.start("user-1", CAP, now=NOW)
    await harness.answer_all(paper.attempt_id, [q.id for q in paper.questions[:23]], right=23)

    result = await harness.service.submit("user-1", paper.attempt_id, now=NOW)

    assert (result.correct_count, result.total, result.passed) == (23, 30, False)


async def test_handing_in_twice_returns_the_same_grade() -> None:
    harness = Harness()
    paper = await harness.service.start("user-1", CAP, now=NOW)
    await harness.answer_all(paper.attempt_id, [q.id for q in paper.questions], right=30)
    first = await harness.service.submit("user-1", paper.attempt_id, now=NOW)

    again = await harness.service.submit("user-1", paper.attempt_id, now=NOW + timedelta(hours=1))

    assert again.submitted_at == first.submitted_at
    assert again.correct_count == first.correct_count == 30
    # Re-applying the cap is how a lost response is recovered; it is a no-op.
    assert harness.game.cleared == [CAP, CAP]


async def test_an_abandoned_sitting_cannot_be_handed_in() -> None:
    harness = Harness()
    first = await harness.service.start("user-1", CAP, now=NOW)
    await harness.service.start("user-1", CAP, now=NOW)

    with pytest.raises(BenchmarkExamAttemptClosedError):
        await harness.service.submit("user-1", first.attempt_id, now=NOW)


# --- the check route lock ---------------------------------------------------------


async def test_questions_on_the_open_paper_are_locked_until_it_closes() -> None:
    harness = Harness(extra=[_select("elsewhere", "bank")])
    paper = await harness.service.start("user-1", CAP, now=NOW)
    question = paper.questions[0].id

    assert await harness.service.is_challenge_locked("user-1", question, now=NOW)
    assert not await harness.service.is_challenge_locked("user-1", "elsewhere", now=NOW)
    assert not await harness.service.is_challenge_locked("user-2", question, now=NOW)

    await harness.service.submit("user-1", paper.attempt_id, now=NOW)

    assert not await harness.service.is_challenge_locked("user-1", question, now=NOW)


# --- the draw ---------------------------------------------------------------------


async def test_the_paper_comes_from_the_course_with_the_most_progress() -> None:
    harness = Harness()
    other = [Lesson(id="o1", unit_id="ou", title="O", order_index=1, is_bank=False)]
    harness.service.courses = FakeCourses(  # type: ignore[assignment]
        [
            Course(id="empty", title="E", image_src=""),
            Course(id="course-1", title="C", image_src=""),
        ]
    )
    harness.service.lessons = FakeLessons(  # type: ignore[assignment]
        {"empty": other, "course-1": harness.lessons}
    )

    await harness.service.start("user-1", CAP, now=NOW)

    assert harness.quiz.drawn_from == ["l0", "l1", "l2"]


async def test_history_lists_sittings_newest_first() -> None:
    harness = Harness()
    first = await harness.service.start("user-1", CAP, now=NOW)
    await harness.service.submit("user-1", first.attempt_id, now=NOW)
    second = await harness.service.start("user-1", CAP, now=NOW + timedelta(minutes=1))

    history = await harness.service.history("user-1")

    assert [row.attempt_id for row in history] == [second.attempt_id, first.attempt_id]
    assert history[1].percent == 0
    assert history[0].percent is None
