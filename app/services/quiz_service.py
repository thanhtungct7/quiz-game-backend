import random

from app.core.exceptions import LessonNotFoundError, UnitNotFoundError
from app.models.challenge import Challenge, ChallengeDifficulty
from app.repository.challenge_repository import ChallengeRepository
from app.repository.lesson_repository import LessonRepository
from app.repository.unit_repository import UnitRepository
from app.schemas.course_content import ChallengeOptionPublicRead, ChallengePublicRead
from app.schemas.quiz import QuizSet, StageQuizSet


class QuizService:
    def __init__(
        self,
        challenges: ChallengeRepository,
        lessons: LessonRepository,
        units: UnitRepository,
    ) -> None:
        self.challenges = challenges
        self.lessons = lessons
        self.units = units

    async def generate_for_lesson(
        self,
        lesson_id: str,
        topic_ids: list[str] | None = None,
        difficulties: list[ChallengeDifficulty] | None = None,
        count: int = 10,
        exclude_ids: list[str] | None = None,
        seed: int | None = None,
    ) -> QuizSet:
        lesson = await self.lessons.get_by_id(lesson_id)
        if lesson is None:
            raise LessonNotFoundError(lesson_id)

        rng = random.Random(seed)  # noqa: S311 -- shuffling quiz questions, not security-sensitive
        questions = await self._draw_questions(
            lesson_id, topic_ids, difficulties, count, exclude_ids, rng
        )
        return QuizSet(
            lesson_id=lesson_id,
            requested_count=count,
            returned_count=len(questions),
            questions=questions,
        )

    async def generate_for_unit(
        self,
        unit_id: str,
        topic_ids: list[str] | None = None,
        difficulties: list[ChallengeDifficulty] | None = None,
        count: int = 10,
        exclude_ids: list[str] | None = None,
        seed: int | None = None,
    ) -> list[StageQuizSet]:
        unit = await self.units.get_by_id(unit_id)
        if unit is None:
            raise UnitNotFoundError(unit_id)

        rng = random.Random(seed)  # noqa: S311 -- shuffling quiz questions, not security-sensitive
        stage_sets: list[StageQuizSet] = []
        for lesson in await self.lessons.list_by_unit(unit_id):
            questions = await self._draw_questions(
                lesson.id, topic_ids, difficulties, count, exclude_ids, rng
            )
            stage_sets.append(
                StageQuizSet(
                    lesson_id=lesson.id,
                    lesson_title=lesson.title,
                    requested_count=count,
                    returned_count=len(questions),
                    questions=questions,
                )
            )
        return stage_sets

    async def _draw_questions(
        self,
        lesson_id: str,
        topic_ids: list[str] | None,
        difficulties: list[ChallengeDifficulty] | None,
        count: int,
        exclude_ids: list[str] | None,
        rng: random.Random,
    ) -> list[ChallengePublicRead]:
        pool = await self.challenges.list_by_lesson_filtered(lesson_id, topic_ids, difficulties)
        if exclude_ids:
            excluded = set(exclude_ids)
            pool = [challenge for challenge in pool if challenge.id not in excluded]

        selected = rng.sample(pool, min(count, len(pool)))
        rng.shuffle(selected)
        return [self._to_public_read(challenge, rng) for challenge in selected]

    @staticmethod
    def _to_public_read(challenge: Challenge, rng: random.Random) -> ChallengePublicRead:
        options = list(challenge.options)
        rng.shuffle(options)
        return ChallengePublicRead(
            id=challenge.id,
            lesson_id=challenge.lesson_id,
            type=challenge.type,
            question=challenge.question,
            difficulty=challenge.difficulty,
            topic_id=challenge.topic_id,
            order_index=challenge.order_index,
            options=[ChallengeOptionPublicRead.model_validate(option) for option in options],
        )
