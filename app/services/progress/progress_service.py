from datetime import UTC, datetime

from app.core.exceptions import (
    ChallengeNotFoundError,
    ChallengeOptionNotFoundError,
    CourseNotFoundError,
    LessonNotFoundError,
    UnitNotFoundError,
)
from app.models.content.challenge_option import ChallengeOption
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


class ProgressService:
    def __init__(
        self,
        progress: UserProgressRepository,
        challenges: ChallengeRepository,
        lessons: LessonRepository,
        units: UnitRepository,
        courses: CourseRepository,
    ) -> None:
        self.progress = progress
        self.challenges = challenges
        self.lessons = lessons
        self.units = units
        self.courses = courses

    async def check_answer(
        self, user_id: str, challenge_id: str, selected_option_id: str
    ) -> AnswerCheckResult:
        challenge = await self.challenges.get_by_id(challenge_id)
        if challenge is None:
            raise ChallengeNotFoundError(challenge_id)

        selected = next(
            (option for option in challenge.options if option.id == selected_option_id), None
        )
        if selected is None:
            raise ChallengeOptionNotFoundError(selected_option_id)

        await self._record_attempt(user_id, challenge.id, challenge.lesson_id, selected)
        await self._recompute_lesson_progress(user_id, challenge.lesson_id)

        return AnswerCheckResult(
            challenge_id=challenge.id,
            selected_option_id=selected.id,
            correct=selected.correct,
            correct_option_ids=[option.id for option in challenge.options if option.correct],
            explanation=challenge.explanation,
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
        self, user_id: str, challenge_id: str, lesson_id: str, selected: ChallengeOption
    ) -> UserChallengeProgress:
        now = datetime.now(UTC)
        existing = await self.progress.get_challenge_progress(user_id, challenge_id)
        if existing is None:
            record = UserChallengeProgress(
                user_id=user_id,
                challenge_id=challenge_id,
                lesson_id=lesson_id,
                last_selected_option_id=selected.id,
                mastered=selected.correct,
                attempts_count=1,
                mastered_at=now if selected.correct else None,
                last_attempted_at=now,
            )
            return await self.progress.create_challenge_progress(record)

        updates: dict[str, object] = {
            "last_selected_option_id": selected.id,
            "attempts_count": existing.attempts_count + 1,
            "last_attempted_at": now,
        }
        if selected.correct and not existing.mastered:
            updates["mastered"] = True
            updates["mastered_at"] = now
        return await self.progress.update_challenge_progress(existing, updates)

    async def _recompute_lesson_progress(
        self, user_id: str, lesson_id: str
    ) -> UserLessonProgress:
        total = await self.challenges.count_by_lesson(lesson_id)
        mastered = await self.progress.count_mastered_challenges(user_id, lesson_id)
        attempted = await self.progress.count_attempted_challenges(user_id, lesson_id)

        if total > 0 and mastered >= total:
            status = LessonProgressStatus.COMPLETED
        elif attempted > 0:
            status = LessonProgressStatus.IN_PROGRESS
        else:
            status = LessonProgressStatus.NOT_STARTED

        now = datetime.now(UTC)
        existing = await self.progress.get_lesson_progress(user_id, lesson_id)
        if existing is None:
            record = UserLessonProgress(
                user_id=user_id,
                lesson_id=lesson_id,
                status=status,
                correct_challenge_count=mastered,
                total_challenge_count=total,
                completed_at=now if status == LessonProgressStatus.COMPLETED else None,
                updated_at=now,
            )
            return await self.progress.create_lesson_progress(record)

        updates: dict[str, object] = {
            "status": status,
            "correct_challenge_count": mastered,
            "total_challenge_count": total,
            "updated_at": now,
        }
        if status == LessonProgressStatus.COMPLETED and existing.completed_at is None:
            updates["completed_at"] = now
        return await self.progress.update_lesson_progress(existing, updates)
