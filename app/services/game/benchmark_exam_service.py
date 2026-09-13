"""Sitting the Benchmark Exam: drawing a paper, taking answers, grading it.

The server is the only party that knows what is on a paper and what is right.
A client is handed the public questions, sends answers one at a time and hears
nothing back about them until the paper is handed in, at which point the grade
-- and, on a pass, a lifted cap -- comes from here. There is no route left that
accepts a verdict from the client.
"""

from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError

from app.core.exceptions import (
    BenchmarkExamAlreadyClearedError,
    BenchmarkExamAttemptClosedError,
    BenchmarkExamAttemptNotFoundError,
    BenchmarkExamNotEligibleError,
    BenchmarkExamNotEnoughQuestionsError,
    BenchmarkExamQuestionAlreadyAnsweredError,
    BenchmarkExamQuestionNotInAttemptError,
    ChallengeNotFoundError,
)
from app.models.game.benchmark_exam_attempt import BenchmarkAttemptStatus, BenchmarkExamAttempt
from app.models.progress.user_lesson_progress import LessonProgressStatus
from app.repository.content.challenge_repository import ChallengeRepository
from app.repository.content.course_repository import CourseRepository
from app.repository.content.lesson_repository import LessonRepository
from app.repository.game.benchmark_exam_repository import BenchmarkExamRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.progress.user_progress_repository import UserProgressRepository
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.game.benchmark_exam import (
    BenchmarkAnswerAck,
    BenchmarkAttemptRead,
    BenchmarkAttemptSummary,
    BenchmarkResultRead,
)
from app.schemas.game.game import GameProfileRead
from app.services.content.grading import grade
from app.services.content.quiz_service import QuizService
from app.services.game.benchmark_exam import (
    MIN_QUESTION_COUNT,
    PASS_PERCENT,
    QUESTION_COUNT,
    QUESTIONS_PER_LESSON,
    TIME_LIMIT,
    PathUnit,
    accepts_answers_at,
    group_path,
    is_passing,
    percent,
    pick_source_units,
)
from app.services.game.cefr import LEVEL_CAPS
from app.services.game.game_service import GameService
from app.services.game.leveling import level_for_exp


class BenchmarkExamService:
    def __init__(
        self,
        *,
        attempts: BenchmarkExamRepository,
        profiles: GameProfileRepository,
        game: GameService,
        quiz: QuizService,
        challenges: ChallengeRepository,
        courses: CourseRepository,
        lessons: LessonRepository,
        progress: UserProgressRepository,
    ) -> None:
        self.attempts = attempts
        self.profiles = profiles
        self.game = game
        self.quiz = quiz
        self.challenges = challenges
        self.courses = courses
        self.lessons = lessons
        self.progress = progress

    # --- sitting -------------------------------------------------------------

    async def start(
        self, user_id: str, cap_level: int, now: datetime | None = None
    ) -> BenchmarkAttemptRead:
        """Open a sitting for one cap, on a freshly drawn paper.

        Refused for anything not in `LEVEL_CAPS`, for a cap the player's raw
        level has not reached -- an exam cannot be sat early -- and for one
        already cleared. Retaking is free and uncapped: a learner stuck behind
        a cap with no way through is the failure mode worth avoiding, and each
        sitting redraws, so it is not the same paper twice.

        A sitting still open is closed. One whose clock has run out is graded
        on what it holds, because those answers were given in time; one still
        running is abandoned ungraded once the new paper is drawn, since
        starting over is the player's own choice.
        """
        now = now or datetime.now(UTC)
        if cap_level not in LEVEL_CAPS:
            raise BenchmarkExamNotEligibleError(f"{cap_level} is not a level cap")

        # Before eligibility: a sitting that ran out of time may be the pass
        # that already cleared this very cap.
        await self._grade_open_sitting_if_expired(user_id, now)
        profile = await self.profiles.get_or_create(user_id)
        if cap_level <= profile.benchmark_cleared_level:
            raise BenchmarkExamAlreadyClearedError(f"Level {cap_level} is already cleared")
        if level_for_exp(profile.total_exp) < cap_level:
            raise BenchmarkExamNotEligibleError(f"Level {cap_level} has not been reached yet")

        questions = await self._draw_paper(user_id)
        if len(questions) < MIN_QUESTION_COUNT:
            raise BenchmarkExamNotEnoughQuestionsError(
                f"Only {len(questions)} questions could be drawn; "
                f"at least {MIN_QUESTION_COUNT} are needed"
            )

        # After the draw, so a paper that cannot be drawn leaves the running
        # sitting alone.
        await self._abandon_open_sitting(user_id)

        attempt = BenchmarkExamAttempt(
            user_id=user_id,
            cap_level=cap_level,
            status=BenchmarkAttemptStatus.IN_PROGRESS,
            question_ids=[question.id for question in questions],
            answers={},
            total_count=len(questions),
            pass_percent=PASS_PERCENT,
            started_at=now,
            expires_at=now + TIME_LIMIT,
        )
        try:
            created = await self.attempts.create(attempt)
        except IntegrityError as exc:
            # The partial unique index: another sitting was opened concurrently.
            raise BenchmarkExamAttemptClosedError(
                "Another sitting was opened at the same time"
            ) from exc

        return BenchmarkAttemptRead(
            attempt_id=created.id,
            cap_level=created.cap_level,
            questions=questions,
            total=created.total_count,
            pass_percent=created.pass_percent,
            started_at=created.started_at,
            expires_at=created.expires_at,
        )

    async def answer(
        self,
        user_id: str,
        attempt_id: str,
        challenge_id: str,
        selected_option_id: str | None = None,
        selected_option_ids: list[str] | None = None,
        now: datetime | None = None,
    ) -> BenchmarkAnswerAck:
        """Take one answer and keep its grade to ourselves.

        Each question on the paper is answered once. Graded against the stored
        challenge the moment it arrives, with exactly the rules a study answer
        is graded by, but never recorded as study.
        """
        now = now or datetime.now(UTC)
        attempt = await self._locked_sitting(user_id, attempt_id)
        if attempt.status is not BenchmarkAttemptStatus.IN_PROGRESS:
            raise BenchmarkExamAttemptClosedError("This sitting is already closed")
        if not accepts_answers_at(attempt.expires_at, now):
            await self._grade(attempt, now)
            raise BenchmarkExamAttemptClosedError("Time is up for this sitting")
        if challenge_id not in attempt.question_ids:
            raise BenchmarkExamQuestionNotInAttemptError(
                f"{challenge_id} is not on this paper"
            )
        if challenge_id in attempt.answers:
            raise BenchmarkExamQuestionAlreadyAnsweredError(
                f"{challenge_id} has already been answered"
            )

        challenge = await self.challenges.get_by_id(challenge_id)
        if challenge is None:
            raise ChallengeNotFoundError(challenge_id)
        graded = grade(challenge, selected_option_id, selected_option_ids)

        answers = {
            **attempt.answers,
            challenge_id: {"submitted": graded.submitted_option_ids, "correct": graded.correct},
        }
        saved = await self.attempts.save(attempt, {"answers": answers})
        return BenchmarkAnswerAck(
            attempt_id=saved.id, answered_count=len(saved.answers), total=saved.total_count
        )

    async def submit(
        self, user_id: str, attempt_id: str, now: datetime | None = None
    ) -> BenchmarkResultRead:
        """Hand the paper in, or read back the grade of one already handed in.

        Questions left unanswered count as wrong. Safe to call again: a graded
        sitting is never regraded, and a pass re-applies its cap, which is a
        no-op once it has landed -- so a submit whose response was lost in
        transit is simply retried.
        """
        now = now or datetime.now(UTC)
        attempt = await self._locked_sitting(user_id, attempt_id)
        if attempt.status is BenchmarkAttemptStatus.ABANDONED:
            raise BenchmarkExamAttemptClosedError("This sitting was abandoned")
        if attempt.status is BenchmarkAttemptStatus.IN_PROGRESS:
            attempt = await self._grade(attempt, now)

        if attempt.status is BenchmarkAttemptStatus.PASSED:
            profile = await self.game.clear_benchmark_cap(user_id, attempt.cap_level)
        else:
            profile = await self.game.get_profile(user_id)
        return _result(attempt, profile)

    async def history(self, user_id: str, limit: int = 20) -> list[BenchmarkAttemptSummary]:
        return [
            BenchmarkAttemptSummary(
                attempt_id=attempt.id,
                cap_level=attempt.cap_level,
                status=attempt.status,
                correct_count=attempt.correct_count,
                total=attempt.total_count,
                percent=(
                    None
                    if attempt.correct_count is None
                    else percent(attempt.correct_count, attempt.total_count)
                ),
                started_at=attempt.started_at,
                submitted_at=attempt.submitted_at,
            )
            for attempt in await self.attempts.list_for_user(user_id, limit)
        ]

    async def is_challenge_locked(
        self, user_id: str, challenge_id: str, now: datetime | None = None
    ) -> bool:
        """Whether checking this challenge would reveal an answer on the
        player's own open paper. Stays locked until the sitting stops taking
        answers, answered or not."""
        now = now or datetime.now(UTC)
        attempt = await self.attempts.get_in_progress(user_id)
        return (
            attempt is not None
            and challenge_id in attempt.question_ids
            and accepts_answers_at(attempt.expires_at, now)
        )

    # --- internals -----------------------------------------------------------

    async def _draw_paper(self, user_id: str) -> list[ChallengePublicRead]:
        """A paper over the course the player has made the most progress in."""
        best_units: list[PathUnit] = []
        best_completed: set[str] = set()
        best_score = -1
        for course in await self.courses.list_all():
            path = await self.lessons.list_path_by_course(course.id)
            if not path:
                continue
            rows = await self.progress.list_lesson_progress(
                user_id, [lesson.id for lesson in path]
            )
            completed = {
                row.lesson_id for row in rows if row.status is LessonProgressStatus.COMPLETED
            }
            if len(completed) > best_score:
                best_units, best_completed, best_score = group_path(path), completed, len(completed)

        sources = pick_source_units(best_units, best_completed)
        lesson_ids = [lesson_id for unit in sources for lesson_id in unit.lesson_ids]
        return await self.quiz.draw_for_benchmark(lesson_ids, QUESTIONS_PER_LESSON, QUESTION_COUNT)

    async def _grade_open_sitting_if_expired(self, user_id: str, now: datetime) -> None:
        locked = await self._lock_open_sitting(user_id)
        if locked is None or accepts_answers_at(locked.expires_at, now):
            return
        graded = await self._grade(locked, now)
        if graded.status is BenchmarkAttemptStatus.PASSED:
            await self.game.clear_benchmark_cap(user_id, graded.cap_level)

    async def _abandon_open_sitting(self, user_id: str) -> None:
        locked = await self._lock_open_sitting(user_id)
        if locked is not None:
            await self.attempts.save(locked, {"status": BenchmarkAttemptStatus.ABANDONED})

    async def _lock_open_sitting(self, user_id: str) -> BenchmarkExamAttempt | None:
        open_sitting = await self.attempts.get_in_progress(user_id)
        if open_sitting is None:
            return None
        locked = await self.attempts.get_for_update(user_id, open_sitting.id)
        if locked is None or locked.status is not BenchmarkAttemptStatus.IN_PROGRESS:
            return None
        return locked

    async def _locked_sitting(self, user_id: str, attempt_id: str) -> BenchmarkExamAttempt:
        attempt = await self.attempts.get_for_update(user_id, attempt_id)
        if attempt is None:
            raise BenchmarkExamAttemptNotFoundError(attempt_id)
        return attempt

    async def _grade(self, attempt: BenchmarkExamAttempt, now: datetime) -> BenchmarkExamAttempt:
        correct = sum(
            1
            for challenge_id, answer in attempt.answers.items()
            if challenge_id in attempt.question_ids and answer.get("correct") is True
        )
        passed = is_passing(correct, attempt.total_count, attempt.pass_percent)
        return await self.attempts.save(
            attempt,
            {
                "status": (
                    BenchmarkAttemptStatus.PASSED if passed else BenchmarkAttemptStatus.FAILED
                ),
                "correct_count": correct,
                "submitted_at": now,
            },
        )


def _result(attempt: BenchmarkExamAttempt, profile: GameProfileRead) -> BenchmarkResultRead:
    correct = attempt.correct_count or 0
    return BenchmarkResultRead(
        attempt_id=attempt.id,
        cap_level=attempt.cap_level,
        status=attempt.status,
        correct_count=correct,
        total=attempt.total_count,
        percent=percent(correct, attempt.total_count),
        pass_percent=attempt.pass_percent,
        passed=attempt.status is BenchmarkAttemptStatus.PASSED,
        submitted_at=attempt.submitted_at,
        profile=profile,
    )
