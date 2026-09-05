from dataclasses import dataclass
from datetime import UTC, datetime

from app.core.exceptions import (
    ChallengeNotFoundError,
    ChallengeOptionNotFoundError,
    CourseNotFoundError,
    InvalidAnswerSubmissionError,
    LessonNotFoundError,
    UnitNotFoundError,
)
from app.models.content.challenge import Challenge, ChallengeType
from app.models.progress.user_challenge_progress import UserChallengeProgress
from app.models.progress.user_lesson_progress import LessonProgressStatus, UserLessonProgress
from app.repository.content.challenge_repository import ChallengeRepository
from app.repository.content.course_repository import CourseRepository
from app.repository.content.lesson_repository import LessonRepository
from app.repository.content.unit_repository import UnitRepository
from app.repository.progress.user_progress_repository import UserProgressRepository
from app.schemas.content.quiz import AnswerCheckResult
from app.schemas.progress.progress import (
    CourseProgressRead,
    LessonProgressRead,
    UnitProgressRead,
)
from app.services.game.lesson_rewards import LessonRewardService


@dataclass(frozen=True)
class _GradedAnswer:
    """One graded submission, in the shape `AnswerCheckResult` needs it."""

    correct: bool
    recorded_option_id: str | None
    submitted_option_ids: list[str]
    correct_option_ids: list[str]


class ProgressService:
    def __init__(
        self,
        progress: UserProgressRepository,
        challenges: ChallengeRepository,
        lessons: LessonRepository,
        units: UnitRepository,
        courses: CourseRepository,
        rewards: LessonRewardService | None = None,
    ) -> None:
        self.progress = progress
        self.challenges = challenges
        self.lessons = lessons
        self.units = units
        self.courses = courses
        # Optional so progress can be read and graded without the game layer
        # wired in; when absent, finishing a lesson simply pays nothing.
        self.rewards = rewards

    async def check_answer(
        self,
        user_id: str,
        challenge_id: str,
        selected_option_id: str | None = None,
        *,
        selected_option_ids: list[str] | None = None,
    ) -> AnswerCheckResult:
        """Grade one solo-mode answer server-side, record the attempt, and
        roll the lesson's progress forward before telling the client the
        result — correctness is never trusted from the request.

        Which of the two arguments carries the answer is decided by the
        challenge type, not by which one the caller happened to fill in: an
        ORDER challenge is graded on `selected_option_ids` (the word tiles in
        the order they were laid down) and a single-choice one on
        `selected_option_id`. Sending the wrong shape is a bad request, never
        a silently mis-graded answer.
        """
        challenge = await self.challenges.get_by_id(challenge_id)
        if challenge is None:
            raise ChallengeNotFoundError(challenge_id)

        if challenge.type is ChallengeType.ORDER:
            graded = self._grade_ordered(challenge, selected_option_ids)
        else:
            graded = self._grade_single_choice(challenge, selected_option_id)

        await self._record_attempt(
            user_id, challenge.id, challenge.lesson_id, graded.recorded_option_id, graded.correct
        )
        await self._recompute_lesson_progress(user_id, challenge.lesson_id)

        return AnswerCheckResult(
            challenge_id=challenge.id,
            selected_option_id=graded.recorded_option_id,
            selected_option_ids=graded.submitted_option_ids,
            correct=graded.correct,
            correct_option_ids=graded.correct_option_ids,
            explanation=challenge.explanation,
        )

    @staticmethod
    def _grade_single_choice(
        challenge: Challenge, selected_option_id: str | None
    ) -> "_GradedAnswer":
        if selected_option_id is None:
            raise InvalidAnswerSubmissionError(
                f"{challenge.type.value} challenges are answered with selected_option_id"
            )
        selected = next(
            (option for option in challenge.options if option.id == selected_option_id), None
        )
        if selected is None:
            raise ChallengeOptionNotFoundError(selected_option_id)

        return _GradedAnswer(
            correct=selected.correct,
            recorded_option_id=selected.id,
            submitted_option_ids=[selected.id],
            correct_option_ids=[
                option.id for option in challenge.options if option.correct
            ],
        )

    @staticmethod
    def _grade_ordered(
        challenge: Challenge, selected_option_ids: list[str] | None
    ) -> "_GradedAnswer":
        """Grade a word-ordering answer against the stored option order.

        Compares the *words*, not the option ids: a sentence can repeat a word
        ("càng ... càng ..."), and two tiles carrying the same text are
        interchangeable — swapping them still spells the right sentence, so
        marking that wrong would be marking a correct answer wrong.

        Every tile has to be used. A partial sequence that happens to prefix
        the solution is not the sentence.
        """
        if selected_option_ids is None:
            raise InvalidAnswerSubmissionError(
                "ORDER challenges are answered with selected_option_ids"
            )

        options_by_id = {option.id: option for option in challenge.options}
        for option_id in selected_option_ids:
            if option_id not in options_by_id:
                raise ChallengeOptionNotFoundError(option_id)
        if len(set(selected_option_ids)) != len(selected_option_ids):
            raise InvalidAnswerSubmissionError("An option may only be placed once")

        solution = sorted(challenge.options, key=lambda option: option.order_index)
        submitted_words = [options_by_id[option_id].text for option_id in selected_option_ids]
        correct = submitted_words == [option.text for option in solution]

        return _GradedAnswer(
            correct=correct,
            # There is no single "selected option" to remember here; the row
            # records that the challenge was attempted and whether it was right.
            recorded_option_id=None,
            submitted_option_ids=list(selected_option_ids),
            # A sequence, not a set: this is the sentence in the right order.
            correct_option_ids=[option.id for option in solution],
        )

    async def get_lesson_progress(self, user_id: str, lesson_id: str) -> LessonProgressRead:
        lesson = await self.lessons.get_by_id(lesson_id)
        if lesson is None:
            raise LessonNotFoundError(lesson_id)

        progress = await self.progress.get_lesson_progress(user_id, lesson_id)
        if progress is None:
            total = await self.challenges.count_by_lesson(lesson_id)
            return LessonProgressRead(
                lesson_id=lesson_id,
                status=LessonProgressStatus.NOT_STARTED,
                correct_challenge_count=0,
                total_challenge_count=total,
                completed_at=None,
            )
        return LessonProgressRead.model_validate(progress)

    async def get_unit_progress(self, user_id: str, unit_id: str) -> UnitProgressRead:
        unit = await self.units.get_by_id(unit_id)
        if unit is None:
            raise UnitNotFoundError(unit_id)

        lessons = await self.lessons.list_by_unit(unit_id)
        results = await self._lesson_progress_for(user_id, [lesson.id for lesson in lessons])
        return UnitProgressRead(unit_id=unit_id, lessons=results)

    async def get_course_progress(self, user_id: str, course_id: str) -> CourseProgressRead:
        """Every path lesson's progress for one course, in one round trip.

        The client used to ask per unit, which meant one request per unit on
        every app launch. Pairs with GET /courses/{id}/tree.
        """
        course = await self.courses.get_by_id(course_id)
        if course is None:
            raise CourseNotFoundError(course_id)

        lessons = await self.lessons.list_path_by_course(course_id)
        results = await self._lesson_progress_for(user_id, [lesson.id for lesson in lessons])
        return CourseProgressRead(course_id=course_id, lessons=results)

    async def _lesson_progress_for(
        self, user_id: str, lesson_ids: list[str]
    ) -> list[LessonProgressRead]:
        """Stored progress rows where they exist, synthesised NOT_STARTED rows
        otherwise -- using two bulk queries rather than one per lesson."""
        rows = await self.progress.list_lesson_progress(user_id, lesson_ids)
        rows_by_lesson_id = {row.lesson_id: row for row in rows}

        missing = [lesson_id for lesson_id in lesson_ids if lesson_id not in rows_by_lesson_id]
        totals = await self.challenges.count_by_lessons(missing)

        results: list[LessonProgressRead] = []
        for lesson_id in lesson_ids:
            row = rows_by_lesson_id.get(lesson_id)
            if row is None:
                results.append(
                    LessonProgressRead(
                        lesson_id=lesson_id,
                        status=LessonProgressStatus.NOT_STARTED,
                        correct_challenge_count=0,
                        total_challenge_count=totals.get(lesson_id, 0),
                        completed_at=None,
                    )
                )
            else:
                results.append(LessonProgressRead.model_validate(row))
        return results

    async def _record_attempt(
        self,
        user_id: str,
        challenge_id: str,
        lesson_id: str,
        selected_option_id: str | None,
        correct: bool,
    ) -> UserChallengeProgress:
        """Upsert one challenge's attempt row.

        `mastered` only ever flips false→true, never back — a later wrong
        answer bumps the attempt count but does not un-master a challenge
        the user already got right once.

        `selected_option_id` is None for an ORDER challenge, whose answer is a
        whole sequence rather than one option; the attempt and its outcome are
        still recorded exactly the same way.
        """
        now = datetime.now(UTC)
        existing = await self.progress.get_challenge_progress(user_id, challenge_id)
        if existing is None:
            record = UserChallengeProgress(
                user_id=user_id,
                challenge_id=challenge_id,
                lesson_id=lesson_id,
                last_selected_option_id=selected_option_id,
                mastered=correct,
                attempts_count=1,
                mastered_at=now if correct else None,
                last_attempted_at=now,
            )
            return await self.progress.create_challenge_progress(record)

        updates: dict[str, object] = {
            "last_selected_option_id": selected_option_id,
            "attempts_count": existing.attempts_count + 1,
            "last_attempted_at": now,
        }
        if correct and not existing.mastered:
            updates["mastered"] = True
            updates["mastered_at"] = now
        return await self.progress.update_challenge_progress(existing, updates)

    async def mark_lesson_completed(self, user_id: str, lesson_id: str) -> UserLessonProgress:
        """Complete a lesson outright, whatever the mastered count says.

        The battle gate is why this exists. On the learn path a lesson is
        fought rather than studied, and a fight ends when the monster falls --
        which is usually long before every challenge in the pool has been
        answered correctly. Recomputing from mastery alone would leave a gate
        the player has just cleared sitting at IN_PROGRESS with the next lesson
        locked behind it, and the only way forward would be to kill a monster
        that is already dead.

        Mastery itself is still recorded honestly: `correct_challenge_count`
        counts only what was actually answered right, so a lesson can quite
        correctly read "complete, 5/10".
        """
        total = await self.challenges.count_by_lesson(lesson_id)
        mastered = await self.progress.count_mastered_challenges(user_id, lesson_id)
        return await self._store_lesson_progress(
            user_id, lesson_id, LessonProgressStatus.COMPLETED, mastered, total
        )

    async def _recompute_lesson_progress(
        self, user_id: str, lesson_id: str
    ) -> UserLessonProgress:
        """Recompute a lesson's status from scratch off the current mastered
        and attempted counts. `completed_at` is set once and never cleared,
        so re-attempting challenges in a finished lesson can't un-complete it."""
        total = await self.challenges.count_by_lesson(lesson_id)
        mastered = await self.progress.count_mastered_challenges(user_id, lesson_id)
        attempted = await self.progress.count_attempted_challenges(user_id, lesson_id)

        if total > 0 and mastered >= total:
            status = LessonProgressStatus.COMPLETED
        elif attempted > 0:
            status = LessonProgressStatus.IN_PROGRESS
        else:
            status = LessonProgressStatus.NOT_STARTED

        return await self._store_lesson_progress(user_id, lesson_id, status, mastered, total)

    async def _store_lesson_progress(
        self,
        user_id: str,
        lesson_id: str,
        status: LessonProgressStatus,
        mastered: int,
        total: int,
    ) -> UserLessonProgress:
        """Write the row and pay the completion, at most once per lesson.

        A lesson that is already COMPLETED must never re-enter this branch:
        `completed_at` doubles as the flag, which is what keeps a replayed gate
        from refilling energy and extending the streak a second time.
        """
        now = datetime.now(UTC)
        existing = await self.progress.get_lesson_progress(user_id, lesson_id)
        if existing is None:
            newly_completed = status == LessonProgressStatus.COMPLETED
            record = UserLessonProgress(
                user_id=user_id,
                lesson_id=lesson_id,
                status=status,
                correct_challenge_count=mastered,
                total_challenge_count=total,
                completed_at=now if newly_completed else None,
                updated_at=now,
            )
            stored = await self.progress.create_lesson_progress(record)
            if newly_completed:
                await self._reward_completion(user_id)
            return stored

        # Completion is a ratchet. A gate cleared at 5/10 is COMPLETED, and the
        # next answer in a replay would otherwise recompute it back down to
        # IN_PROGRESS and re-lock the lesson behind it.
        if existing.completed_at is not None:
            status = LessonProgressStatus.COMPLETED

        updates: dict[str, object] = {
            "status": status,
            "correct_challenge_count": mastered,
            "total_challenge_count": total,
            "updated_at": now,
        }
        # `completed_at` is set once and never cleared, so it doubles as the
        # flag for "this is the first time" -- redoing a finished lesson must
        # not pay out again.
        newly_completed = (
            status == LessonProgressStatus.COMPLETED and existing.completed_at is None
        )
        if newly_completed:
            updates["completed_at"] = now
        stored = await self.progress.update_lesson_progress(existing, updates)
        if newly_completed:
            await self._reward_completion(user_id)
        return stored

    async def _reward_completion(self, user_id: str) -> None:
        """Finishing a lesson refills energy and counts toward the streak."""
        if self.rewards is None:
            return
        await self.rewards.on_lesson_completed(user_id)
