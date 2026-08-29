from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.content.lesson import Lesson
from app.models.progress.user_challenge_progress import UserChallengeProgress
from app.models.progress.user_lesson_progress import LessonProgressStatus, UserLessonProgress


class UserProgressRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # --- Challenge progress --------------------------------------------------

    async def get_challenge_progress(
        self, user_id: str, challenge_id: str
    ) -> UserChallengeProgress | None:
        statement = select(UserChallengeProgress).where(
            UserChallengeProgress.user_id == user_id,
            UserChallengeProgress.challenge_id == challenge_id,
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def create_challenge_progress(
        self, progress: UserChallengeProgress
    ) -> UserChallengeProgress:
        self.db.add(progress)
        await self.db.commit()
        await self.db.refresh(progress)
        return progress

    async def update_challenge_progress(
        self, progress: UserChallengeProgress, data: dict[str, object]
    ) -> UserChallengeProgress:
        for field, value in data.items():
            setattr(progress, field, value)
        await self.db.commit()
        await self.db.refresh(progress)
        return progress

    async def completed_unit_ids(self, user_id: str) -> set[str]:
        """Units where this user has COMPLETED every lesson on the path.

        Bank lessons are excluded, exactly as `LessonRepository.list_path_by_course`
        excludes them: a bank lesson holds thousands of leftover imported
        questions, so counting it would make every unit permanently unfinished.

        Derived on demand rather than stored. There is no unit-completion table
        and adding one would mean keeping it in step with every lesson write;
        this cannot fall out of date because it is recomputed from the lessons
        themselves each time it is asked.
        """
        completed = func.count().filter(
            UserLessonProgress.status == LessonProgressStatus.COMPLETED
        )
        statement = (
            select(Lesson.unit_id)
            .select_from(Lesson)
            .outerjoin(
                UserLessonProgress,
                (UserLessonProgress.lesson_id == Lesson.id)
                & (UserLessonProgress.user_id == user_id),
            )
            .where(Lesson.is_bank.is_(False))
            .group_by(Lesson.unit_id)
            .having(func.count() == completed)
        )
        result = await self.db.execute(statement)
        return set(result.scalars().all())

    async def count_attempted_challenges(self, user_id: str, lesson_id: str) -> int:
        statement = (
            select(func.count())
            .select_from(UserChallengeProgress)
            .where(
                UserChallengeProgress.user_id == user_id,
                UserChallengeProgress.lesson_id == lesson_id,
            )
        )
        result = await self.db.execute(statement)
        return int(result.scalar_one())

    async def count_mastered_challenges(self, user_id: str, lesson_id: str) -> int:
        statement = (
            select(func.count())
            .select_from(UserChallengeProgress)
            .where(
                UserChallengeProgress.user_id == user_id,
                UserChallengeProgress.lesson_id == lesson_id,
                UserChallengeProgress.mastered.is_(True),
            )
        )
        result = await self.db.execute(statement)
        return int(result.scalar_one())

    # --- Lesson progress -------------------------------------------------------

    async def get_lesson_progress(
        self, user_id: str, lesson_id: str
    ) -> UserLessonProgress | None:
        statement = select(UserLessonProgress).where(
            UserLessonProgress.user_id == user_id,
            UserLessonProgress.lesson_id == lesson_id,
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def list_lesson_progress(
        self, user_id: str, lesson_ids: list[str]
    ) -> list[UserLessonProgress]:
        if not lesson_ids:
            return []
        statement = select(UserLessonProgress).where(
            UserLessonProgress.user_id == user_id,
            UserLessonProgress.lesson_id.in_(lesson_ids),
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def create_lesson_progress(self, progress: UserLessonProgress) -> UserLessonProgress:
        self.db.add(progress)
        await self.db.commit()
        await self.db.refresh(progress)
        return progress

    async def update_lesson_progress(
        self, progress: UserLessonProgress, data: dict[str, object]
    ) -> UserLessonProgress:
        for field, value in data.items():
            setattr(progress, field, value)
        await self.db.commit()
        await self.db.refresh(progress)
        return progress
