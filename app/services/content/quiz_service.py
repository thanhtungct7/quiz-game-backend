import random

from app.core.exceptions import LessonNotFoundError, UnitNotFoundError
from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.repository.content.challenge_repository import ChallengeRepository
from app.repository.content.lesson_repository import LessonRepository
from app.repository.content.unit_repository import UnitRepository
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.content.quiz import QuizSet, QuizSetWithAnswers, StageQuizSet
from app.services.content.challenge_presenter import to_public_challenge


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
        """One shuffled quiz for a single lesson, filtered by topic/difficulty
        and optionally excluding challenges already seen."""
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
        """One quiz set per lesson in the unit, sharing a single RNG so a
        given seed reproduces the whole unit's draw deterministically."""
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
        dropped rather than served as unanswerable rounds, and so are ORDER
        ones: a duo round is a single timed tap, which cannot express a word
        order, and every tile of an ORDER challenge is flagged correct -- left
        in, they would be rounds where any tap scores.
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
            if challenge.type is ChallengeType.ORDER:
                continue
            correct_ids = [option.id for option in challenge.options if option.correct]
            if not correct_ids:
                continue
            questions.append(to_public_challenge(challenge, rng))
            answer_key[challenge.id] = correct_ids
            explanations[challenge.id] = challenge.explanation

        return QuizSetWithAnswers(
            questions=questions, answer_key=answer_key, explanations=explanations
        )

    async def generate_for_battle(
        self,
        lesson_id: str,
        count: int = 20,
        seed: int | None = None,
    ) -> QuizSetWithAnswers:
        """Draw one lesson's questions for a PvE battle, key included.

        The key is never used to grade: a battle answer goes through
        `ProgressService.check_answer` so it counts as study exactly like the
        ordinary lesson screen. It is here for the two things the engine has to
        do without a graded answer -- revealing the solution when the clock
        runs out, and telling a REMOVE_OPTIONS skill which options are wrong.

        ORDER challenges are drawn like any other: a battle round is answered
        on the same word-tile surface the lesson screen uses, and whole lessons
        ("Ghép câu") hold nothing else. Only challenges with no answer at all
        are dropped, rather than served as unanswerable rounds.
        """
        lesson = await self.lessons.get_by_id(lesson_id)
        if lesson is None:
            raise LessonNotFoundError(lesson_id)

        rng = random.Random(seed)  # noqa: S311 -- shuffling quiz questions, not security-sensitive
        # Over-fetch so dropping malformed challenges still leaves a full fight.
        pool = await self.challenges.list_by_lesson_filtered(
            lesson_id, None, None, limit=count * 2
        )
        rng.shuffle(pool)

        questions: list[ChallengePublicRead] = []
        answer_key: dict[str, list[str]] = {}
        explanations: dict[str, str | None] = {}
        for challenge in pool:
            if len(questions) == count:
                break
            correct_ids = _battle_answer_key(challenge)
            if not correct_ids:
                continue
            questions.append(to_public_challenge(challenge, rng))
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
        """Sample `count` distinct challenges for one lesson and shuffle
        both the question order and each question's options."""
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
        return [to_public_challenge(challenge, rng) for challenge in selected]


def _battle_answer_key(challenge: Challenge) -> list[str]:
    """The option ids a battle grades this challenge against.

    For a single-choice challenge that is its correct options -- normally one,
    and order means nothing. For an ORDER challenge every tile is flagged
    correct, so a set of them says nothing at all; the key carries the solution
    *in order* instead, which is what `order_index` spells out. An empty list
    either way means the challenge has no answer to grade against and must not
    be served.
    """
    if challenge.type is ChallengeType.ORDER:
        return [
            option.id for option in sorted(challenge.options, key=lambda o: o.order_index)
        ]
    return [option.id for option in challenge.options if option.correct]
