from app.core.exceptions import (
    ChallengeNotFoundError,
    ChallengeOptionNotFoundError,
    CourseNotFoundError,
    DuplicateOrderIndexError,
    DuplicateTopicNameError,
    InvalidChallengeOptionsError,
    LessonNotFoundError,
    TopicNotFoundError,
    UnitNotFoundError,
)
from app.models.challenge import Challenge, ChallengeType
from app.models.challenge_option import ChallengeOption
from app.models.course import Course
from app.models.lesson import Lesson
from app.models.topic import Topic
from app.models.unit import Unit
from app.repository.challenge_option_repository import ChallengeOptionRepository
from app.repository.challenge_repository import ChallengeRepository
from app.repository.course_repository import CourseRepository
from app.repository.lesson_repository import LessonRepository
from app.repository.topic_repository import TopicRepository
from app.repository.unit_repository import UnitRepository
from app.schemas.course_content import (
    ChallengeCreate,
    ChallengeOptionCreate,
    ChallengeOptionUpdate,
    ChallengeUpdate,
    CourseCreate,
    CourseUpdate,
    LessonCreate,
    LessonUpdate,
    TopicCreate,
    TopicStats,
    TopicUpdate,
    UnitCreate,
    UnitUpdate,
)

_DIFFICULTIES = ("EASY", "MEDIUM", "HARD")
_UNASSIGNED_TOPIC_NAME = "Chưa phân loại"


def _validate_correct_flags(challenge_type: ChallengeType, correct_flags: list[bool]) -> None:
    correct_count = sum(1 for flag in correct_flags if flag)
    if correct_count == 0:
        raise InvalidChallengeOptionsError("At least one option must be correct")
    if challenge_type == ChallengeType.SELECT and correct_count != 1:
        raise InvalidChallengeOptionsError(
            "SELECT challenges require exactly one correct option"
        )


def _validate_unique_option_orders(order_indexes: list[int]) -> None:
    if len(order_indexes) != len(set(order_indexes)):
        raise DuplicateOrderIndexError("Challenge options must have unique order_index values")


class CourseContentService:
    def __init__(
        self,
        courses: CourseRepository,
        units: UnitRepository,
        lessons: LessonRepository,
        challenges: ChallengeRepository,
        challenge_options: ChallengeOptionRepository,
        topics: TopicRepository,
    ) -> None:
        self.courses = courses
        self.units = units
        self.lessons = lessons
        self.challenges = challenges
        self.challenge_options = challenge_options
        self.topics = topics

    # --- Courses ---------------------------------------------------------

    async def create_course(self, data: CourseCreate) -> Course:
        return await self.courses.create(Course(**data.model_dump()))

    async def list_courses(self) -> list[Course]:
        return await self.courses.list_all()

    async def get_course(self, course_id: str) -> Course:
        course = await self.courses.get_by_id(course_id)
        if course is None:
            raise CourseNotFoundError(course_id)
        return course

    async def update_course(self, course_id: str, data: CourseUpdate) -> Course:
        course = await self.get_course(course_id)
        updates = data.model_dump(exclude_unset=True)
        return await self.courses.update(course, updates)

    async def delete_course(self, course_id: str) -> None:
        course = await self.get_course(course_id)
        await self.courses.delete(course)

    # --- Units -------------------------------------------------------------

    async def create_unit(self, data: UnitCreate) -> Unit:
        await self.get_course(data.course_id)
        clashing = await self.units.get_by_course_and_order(data.course_id, data.order_index)
        if clashing is not None:
            raise DuplicateOrderIndexError(
                f"Unit order {data.order_index} already used in this course"
            )
        return await self.units.create(Unit(**data.model_dump()))

    async def list_units(self, course_id: str) -> list[Unit]:
        return await self.units.list_by_course(course_id)

    async def get_unit(self, unit_id: str) -> Unit:
        unit = await self.units.get_by_id(unit_id)
        if unit is None:
            raise UnitNotFoundError(unit_id)
        return unit

    async def update_unit(self, unit_id: str, data: UnitUpdate) -> Unit:
        unit = await self.get_unit(unit_id)
        updates = data.model_dump(exclude_unset=True)
        new_order = updates.get("order_index")
        if new_order is not None and new_order != unit.order_index:
            clashing = await self.units.get_by_course_and_order(unit.course_id, new_order)
            if clashing is not None and clashing.id != unit.id:
                raise DuplicateOrderIndexError(
                    f"Unit order {new_order} already used in this course"
                )
        return await self.units.update(unit, updates)

    async def delete_unit(self, unit_id: str) -> None:
        unit = await self.get_unit(unit_id)
        await self.units.delete(unit)

    # --- Lessons -----------------------------------------------------------

    async def create_lesson(self, data: LessonCreate) -> Lesson:
        await self.get_unit(data.unit_id)
        clashing = await self.lessons.get_by_unit_and_order(data.unit_id, data.order_index)
        if clashing is not None:
            raise DuplicateOrderIndexError(
                f"Lesson order {data.order_index} already used in this unit"
            )
        return await self.lessons.create(Lesson(**data.model_dump()))

    async def list_lessons(self, unit_id: str) -> list[Lesson]:
        return await self.lessons.list_by_unit(unit_id)

    async def get_lesson(self, lesson_id: str) -> Lesson:
        lesson = await self.lessons.get_by_id(lesson_id)
        if lesson is None:
            raise LessonNotFoundError(lesson_id)
        return lesson

    async def update_lesson(self, lesson_id: str, data: LessonUpdate) -> Lesson:
        lesson = await self.get_lesson(lesson_id)
        updates = data.model_dump(exclude_unset=True)
        new_order = updates.get("order_index")
        if new_order is not None and new_order != lesson.order_index:
            clashing = await self.lessons.get_by_unit_and_order(lesson.unit_id, new_order)
            if clashing is not None and clashing.id != lesson.id:
                raise DuplicateOrderIndexError(
                    f"Lesson order {new_order} already used in this unit"
                )
        return await self.lessons.update(lesson, updates)

    async def delete_lesson(self, lesson_id: str) -> None:
        lesson = await self.get_lesson(lesson_id)
        await self.lessons.delete(lesson)

    # --- Challenges --------------------------------------------------------

    async def create_challenge(self, data: ChallengeCreate) -> Challenge:
        await self.get_lesson(data.lesson_id)
        if data.topic_id is not None:
            await self.get_topic(data.topic_id)
        clashing = await self.challenges.get_by_lesson_and_order(
            data.lesson_id, data.order_index
        )
        if clashing is not None:
            raise DuplicateOrderIndexError(
                f"Challenge order {data.order_index} already used in this lesson"
            )
        _validate_correct_flags(data.type, [option.correct for option in data.options])
        _validate_unique_option_orders([option.order_index for option in data.options])

        challenge = Challenge(
            lesson_id=data.lesson_id,
            type=data.type,
            question=data.question,
            explanation=data.explanation,
            difficulty=data.difficulty,
            topic_id=data.topic_id,
            order_index=data.order_index,
            options=[ChallengeOption(**option.model_dump()) for option in data.options],
            correct_text=data.correct_text,
            tags=data.tags,
            cefr_level=data.cefr_level,
            toeic_band=data.toeic_band,
            toeic_min_score=data.toeic_min_score,
        )
        return await self.challenges.create(challenge)

    async def list_challenges(self, lesson_id: str) -> list[Challenge]:
        return await self.challenges.list_by_lesson(lesson_id)

    async def get_challenge(self, challenge_id: str) -> Challenge:
        challenge = await self.challenges.get_by_id(challenge_id)
        if challenge is None:
            raise ChallengeNotFoundError(challenge_id)
        return challenge

    async def update_challenge(self, challenge_id: str, data: ChallengeUpdate) -> Challenge:
        challenge = await self.get_challenge(challenge_id)
        updates = data.model_dump(exclude_unset=True)

        new_topic_id = updates.get("topic_id")
        if new_topic_id is not None:
            await self.get_topic(new_topic_id)

        new_order = updates.get("order_index")
        if new_order is not None and new_order != challenge.order_index:
            clashing = await self.challenges.get_by_lesson_and_order(
                challenge.lesson_id, new_order
            )
            if clashing is not None and clashing.id != challenge.id:
                raise DuplicateOrderIndexError(
                    f"Challenge order {new_order} already used in this lesson"
                )

        new_type = updates.get("type")
        if new_type is not None and new_type != challenge.type:
            _validate_correct_flags(new_type, [option.correct for option in challenge.options])

        return await self.challenges.update(challenge, updates)

    async def delete_challenge(self, challenge_id: str) -> None:
        challenge = await self.get_challenge(challenge_id)
        await self.challenges.delete(challenge)

    # --- Challenge options ---------------------------------------------------

    async def create_challenge_option(
        self, challenge_id: str, data: ChallengeOptionCreate
    ) -> ChallengeOption:
        challenge = await self.get_challenge(challenge_id)
        existing = await self.challenge_options.list_by_challenge(challenge_id)

        clashing = next((o for o in existing if o.order_index == data.order_index), None)
        if clashing is not None:
            raise DuplicateOrderIndexError(
                f"Option order {data.order_index} already used in this challenge"
            )

        flags = [option.correct for option in existing] + [data.correct]
        _validate_correct_flags(challenge.type, flags)

        option = ChallengeOption(challenge_id=challenge_id, **data.model_dump())
        return await self.challenge_options.create(option)

    async def list_challenge_options(self, challenge_id: str) -> list[ChallengeOption]:
        await self.get_challenge(challenge_id)
        return await self.challenge_options.list_by_challenge(challenge_id)

    async def get_challenge_option(self, option_id: str) -> ChallengeOption:
        option = await self.challenge_options.get_by_id(option_id)
        if option is None:
            raise ChallengeOptionNotFoundError(option_id)
        return option

    async def update_challenge_option(
        self, option_id: str, data: ChallengeOptionUpdate
    ) -> ChallengeOption:
        option = await self.get_challenge_option(option_id)
        updates = data.model_dump(exclude_unset=True)
        siblings: list[ChallengeOption] | None = None

        new_order = updates.get("order_index")
        if new_order is not None and new_order != option.order_index:
            siblings = await self.challenge_options.list_by_challenge(option.challenge_id)
            clashing = next(
                (s for s in siblings if s.order_index == new_order and s.id != option.id), None
            )
            if clashing is not None:
                raise DuplicateOrderIndexError(
                    f"Option order {new_order} already used in this challenge"
                )

        if "correct" in updates:
            challenge = await self.get_challenge(option.challenge_id)
            if siblings is None:
                siblings = await self.challenge_options.list_by_challenge(option.challenge_id)
            flags = [
                updates["correct"] if sibling.id == option.id else sibling.correct
                for sibling in siblings
            ]
            _validate_correct_flags(challenge.type, flags)

        return await self.challenge_options.update(option, updates)

    async def delete_challenge_option(self, option_id: str) -> None:
        option = await self.get_challenge_option(option_id)
        challenge = await self.get_challenge(option.challenge_id)
        siblings = await self.challenge_options.list_by_challenge(option.challenge_id)
        remaining = [sibling for sibling in siblings if sibling.id != option.id]

        if len(remaining) < 2:
            raise InvalidChallengeOptionsError("A challenge must keep at least two options")
        _validate_correct_flags(challenge.type, [sibling.correct for sibling in remaining])

        await self.challenge_options.delete(option)

    # --- Topics --------------------------------------------------------------

    async def create_topic(self, data: TopicCreate) -> Topic:
        existing = await self.topics.get_by_name(data.name)
        if existing is not None:
            raise DuplicateTopicNameError(f"Topic '{data.name}' already exists")
        return await self.topics.create(Topic(**data.model_dump()))

    async def list_topics(self) -> list[Topic]:
        return await self.topics.list_all()

    async def get_topic(self, topic_id: str) -> Topic:
        topic = await self.topics.get_by_id(topic_id)
        if topic is None:
            raise TopicNotFoundError(topic_id)
        return topic

    async def update_topic(self, topic_id: str, data: TopicUpdate) -> Topic:
        topic = await self.get_topic(topic_id)
        updates = data.model_dump(exclude_unset=True)
        new_name = updates.get("name")
        if new_name is not None and new_name != topic.name:
            clashing = await self.topics.get_by_name(new_name)
            if clashing is not None and clashing.id != topic.id:
                raise DuplicateTopicNameError(f"Topic '{new_name}' already exists")
        return await self.topics.update(topic, updates)

    async def delete_topic(self, topic_id: str) -> None:
        topic = await self.get_topic(topic_id)
        await self.topics.delete(topic)

    async def list_challenges_by_topic(self, topic_id: str) -> list[Challenge]:
        await self.get_topic(topic_id)
        return await self.challenges.list_by_topic(topic_id)

    async def get_topic_stats(self) -> list[TopicStats]:
        """Question count per topic, broken down by difficulty. Challenges
        without a topic are grouped under the "unassigned" bucket so nothing
        is silently dropped from the count."""
        challenges = await self.challenges.list_all()
        topic_names = {topic.id: topic.name for topic in await self.topics.list_all()}

        buckets: dict[str | None, dict[str, int]] = {}
        for challenge in challenges:
            bucket = buckets.setdefault(challenge.topic_id, dict.fromkeys(_DIFFICULTIES, 0))
            bucket[challenge.difficulty.value] += 1

        stats = [
            TopicStats(
                topic_id=topic_id,
                topic_name=topic_names.get(topic_id, _UNASSIGNED_TOPIC_NAME)
                if topic_id is not None
                else _UNASSIGNED_TOPIC_NAME,
                total=sum(counts.values()),
                by_difficulty=counts,
            )
            for topic_id, counts in buckets.items()
        ]
        stats.sort(key=lambda stat: stat.topic_name)
        return stats
