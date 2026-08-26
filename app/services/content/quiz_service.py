import random

from app.core.exceptions import LessonNotFoundError, UnitNotFoundError
from app.models.content.challenge import Challenge, ChallengeDifficulty
from app.repository.content.challenge_repository import ChallengeRepository
from app.repository.content.lesson_repository import LessonRepository
from app.repository.content.unit_repository import UnitRepository
from app.schemas.content.course_content import (
    ChallengeOptionPublicRead,
    ChallengePublicRead,
    PassageRead,
)
from app.schemas.content.quiz import QuizSet, QuizSetWithAnswers, StageQuizSet


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

    async def generate_for_duo(
        self,
        count: int,
        topic_ids: list[str] | None = None,
        difficulties: list[ChallengeDifficulty] | None = None,
        seed: int | None = None,
    ) -> QuizSetWithAnswers:
        """Draw one question set shared by both players of a duo match.

        Returns the public questions alongside the answer key so the match
        runtime can grade in memory. Challenges with no correct option are
        dropped rather than served as unanswerable rounds.
        """
        rng = random.Random(seed)  # noqa: S311 -- shuffling quiz questions, not security-sensitive
        # Over-fetch so dropping malformed challenges still leaves enough rounds.
        pool = await self.challenges.list_random_filtered(count * 3, topic_ids, difficulties)

        questions: list[ChallengePublicRead] = []
        answer_key: dict[str, list[str]] = {}
        explanations: dict[str, str | None] = {}
        for challenge in pool:
            if len(questions) == count:
                break
            correct_ids = [option.id for option in challenge.options if option.correct]
            if not correct_ids:
                continue
            questions.append(self._to_public_read(challenge, rng))
            answer_key[challenge.id] = correct_ids
            explanations[challenge.id] = challenge.explanation

        return QuizSetWithAnswers(
            questions=questions, answer_key=answer_key, explanations=explanations
        )

    async def _draw_questions(
        self,
        lesson_id: str,
        topic_ids: list[str] | None,
        difficulties: list[ChallengeDifficulty] | None,
        count: int,
        exclude_ids: list[str] | None,
        rng: random.Random,
    ) -> list[ChallengePublicRead]:
        # Over-fetch so excludes still leave enough to draw from, but stay bounded:
        # a bank lesson holds tens of thousands of challenges.
        pool_limit = count * 5 + len(exclude_ids or ())
        pool = await self.challenges.list_by_lesson_filtered(
            lesson_id, topic_ids, difficulties, limit=pool_limit
        )
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
            passage=PassageRead.model_validate(challenge.passage) if challenge.passage else None,
            options=[ChallengeOptionPublicRead.model_validate(option) for option in options],
        )
