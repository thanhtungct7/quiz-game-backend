import pytest

from app.core.exceptions import (
    ChallengeNotFoundError,
    CourseNotFoundError,
    DuplicateOrderIndexError,
    DuplicateTopicNameError,
    InvalidChallengeOptionsError,
    TopicNotFoundError,
)
from app.models.content.challenge import Challenge, ChallengeDifficulty, ChallengeType
from app.models.content.challenge_option import ChallengeOption
from app.models.content.course import Course
from app.models.content.lesson import Lesson
from app.models.content.topic import Topic
from app.models.content.unit import Unit
from app.schemas.content.course_content import (
    ChallengeCreate,
    ChallengeOptionCreate,
    ChallengeOptionUpdate,
    ChallengeUpdate,
    CourseUpdate,
    TopicCreate,
    TopicUpdate,
    UnitCreate,
)
from app.services.content.course_content_service import CourseContentService


class FakeCourseRepository:
    def __init__(self, courses: list[Course] | None = None) -> None:
        self.courses = {course.id: course for course in courses or []}

    async def create(self, course: Course) -> Course:
        course.id = course.id or f"course-{len(self.courses) + 1}"
        self.courses[course.id] = course
        return course

    async def list_all(self) -> list[Course]:
        return list(self.courses.values())

    async def get_by_id(self, course_id: str) -> Course | None:
        return self.courses.get(course_id)

    async def update(self, course: Course, data: dict[str, object]) -> Course:
        for field, value in data.items():
            setattr(course, field, value)
        return course

    async def delete(self, course: Course) -> None:
        self.courses.pop(course.id, None)


class FakeUnitRepository:
    def __init__(self, units: list[Unit] | None = None) -> None:
        self.units = {unit.id: unit for unit in units or []}

    async def create(self, unit: Unit) -> Unit:
        unit.id = unit.id or f"unit-{len(self.units) + 1}"
        self.units[unit.id] = unit
        return unit

    async def list_by_course(self, course_id: str) -> list[Unit]:
        return [unit for unit in self.units.values() if unit.course_id == course_id]

    async def get_by_id(self, unit_id: str) -> Unit | None:
        return self.units.get(unit_id)

    async def get_by_course_and_order(self, course_id: str, order_index: int) -> Unit | None:
        for unit in self.units.values():
            if unit.course_id == course_id and unit.order_index == order_index:
                return unit
        return None

    async def update(self, unit: Unit, data: dict[str, object]) -> Unit:
        for field, value in data.items():
            setattr(unit, field, value)
        return unit

    async def delete(self, unit: Unit) -> None:
        self.units.pop(unit.id, None)


class FakeLessonRepository:
    def __init__(self) -> None:
        self.lessons: dict[str, Lesson] = {}

    async def create(self, lesson: Lesson) -> Lesson:
        lesson.id = lesson.id or f"lesson-{len(self.lessons) + 1}"
        self.lessons[lesson.id] = lesson
        return lesson

    async def list_by_unit(self, unit_id: str) -> list[Lesson]:
        return [lesson for lesson in self.lessons.values() if lesson.unit_id == unit_id]

    async def get_by_id(self, lesson_id: str) -> Lesson | None:
        return self.lessons.get(lesson_id)

    async def get_by_unit_and_order(self, unit_id: str, order_index: int) -> Lesson | None:
        for lesson in self.lessons.values():
            if lesson.unit_id == unit_id and lesson.order_index == order_index:
                return lesson
        return None

    async def update(self, lesson: Lesson, data: dict[str, object]) -> Lesson:
        for field, value in data.items():
            setattr(lesson, field, value)
        return lesson

    async def delete(self, lesson: Lesson) -> None:
        self.lessons.pop(lesson.id, None)


class FakeChallengeRepository:
    """Mimics the real repository's cascade insert: creating a challenge also
    persists its nested options into the challenge_options table."""

    def __init__(self, challenge_options: "FakeChallengeOptionRepository") -> None:
        self.challenges: dict[str, Challenge] = {}
        self.challenge_options = challenge_options

    async def create(self, challenge: Challenge) -> Challenge:
        challenge.id = challenge.id or f"challenge-{len(self.challenges) + 1}"
        for option in challenge.options:
            option.challenge_id = challenge.id
            option.id = option.id or f"option-{len(self.challenge_options.options) + 1}"
            self.challenge_options.options[option.id] = option
        self.challenges[challenge.id] = challenge
        return challenge

    async def list_by_lesson(self, lesson_id: str) -> list[Challenge]:
        return [c for c in self.challenges.values() if c.lesson_id == lesson_id]

    async def list_by_topic(self, topic_id: str) -> list[Challenge]:
        return [c for c in self.challenges.values() if c.topic_id == topic_id]

    async def list_all(self) -> list[Challenge]:
        return list(self.challenges.values())

    async def get_by_id(self, challenge_id: str) -> Challenge | None:
        return self.challenges.get(challenge_id)

    async def get_by_lesson_and_order(
        self, lesson_id: str, order_index: int
    ) -> Challenge | None:
        for challenge in self.challenges.values():
            if challenge.lesson_id == lesson_id and challenge.order_index == order_index:
                return challenge
        return None

    async def update(self, challenge: Challenge, data: dict[str, object]) -> Challenge:
        for field, value in data.items():
            setattr(challenge, field, value)
        return challenge

    async def delete(self, challenge: Challenge) -> None:
        self.challenges.pop(challenge.id, None)


class FakeChallengeOptionRepository:
    def __init__(self) -> None:
        self.options: dict[str, ChallengeOption] = {}

    async def create(self, option: ChallengeOption) -> ChallengeOption:
        option.id = option.id or f"option-{len(self.options) + 1}"
        self.options[option.id] = option
        return option

    async def list_by_challenge(self, challenge_id: str) -> list[ChallengeOption]:
        return [o for o in self.options.values() if o.challenge_id == challenge_id]

    async def get_by_id(self, option_id: str) -> ChallengeOption | None:
        return self.options.get(option_id)

    async def update(
        self, option: ChallengeOption, data: dict[str, object]
    ) -> ChallengeOption:
        for field, value in data.items():
            setattr(option, field, value)
        return option

    async def delete(self, option: ChallengeOption) -> None:
        self.options.pop(option.id, None)


class FakeTopicRepository:
    def __init__(self) -> None:
        self.topics: dict[str, Topic] = {}

    async def create(self, topic: Topic) -> Topic:
        topic.id = topic.id or f"topic-{len(self.topics) + 1}"
        self.topics[topic.id] = topic
        return topic

    async def list_all(self) -> list[Topic]:
        return sorted(self.topics.values(), key=lambda t: t.name)

    async def get_by_id(self, topic_id: str) -> Topic | None:
        return self.topics.get(topic_id)

    async def get_by_name(self, name: str) -> Topic | None:
        return next((t for t in self.topics.values() if t.name == name), None)

    async def update(self, topic: Topic, data: dict[str, object]) -> Topic:
        for field, value in data.items():
            setattr(topic, field, value)
        return topic

    async def delete(self, topic: Topic) -> None:
        self.topics.pop(topic.id, None)


def build_service() -> tuple[
    CourseContentService,
    FakeCourseRepository,
    FakeUnitRepository,
    FakeLessonRepository,
    FakeChallengeRepository,
    FakeChallengeOptionRepository,
]:
    courses = FakeCourseRepository()
    units = FakeUnitRepository()
    lessons = FakeLessonRepository()
    challenge_options = FakeChallengeOptionRepository()
    challenges = FakeChallengeRepository(challenge_options)
    topics = FakeTopicRepository()
    service = CourseContentService(
        courses=courses,  # type: ignore[arg-type]
        units=units,  # type: ignore[arg-type]
        lessons=lessons,  # type: ignore[arg-type]
        challenges=challenges,  # type: ignore[arg-type]
        challenge_options=challenge_options,  # type: ignore[arg-type]
        topics=topics,  # type: ignore[arg-type]
    )
    return service, courses, units, lessons, challenges, challenge_options


async def _seed_lesson(service: CourseContentService, units: FakeUnitRepository) -> str:
    course = await service.courses.create(
        Course(id="course-1", title="Spanish", image_src="/es.svg")
    )
    unit = await units.create(
        Unit(id="unit-1", course_id=course.id, title="Unit 1", description="d", order_index=1)
    )
    lesson = await service.lessons.create(
        Lesson(id="lesson-1", unit_id=unit.id, title="Lesson 1", order_index=1)
    )
    return lesson.id


@pytest.mark.asyncio
async def test_create_challenge_rejects_select_with_two_correct_options() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)

    payload = ChallengeCreate(
        lesson_id=lesson_id,
        type=ChallengeType.SELECT,
        question="Pick one",
        order_index=1,
        options=[
            ChallengeOptionCreate(text="A", correct=True, order_index=1),
            ChallengeOptionCreate(text="B", correct=True, order_index=2),
        ],
    )

    with pytest.raises(InvalidChallengeOptionsError):
        await service.create_challenge(payload)


@pytest.mark.asyncio
async def test_create_challenge_rejects_duplicate_order_index() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    payload = ChallengeCreate(
        lesson_id=lesson_id,
        type=ChallengeType.SELECT,
        question="Q1",
        order_index=1,
        options=[
            ChallengeOptionCreate(text="A", correct=True, order_index=1),
            ChallengeOptionCreate(text="B", correct=False, order_index=2),
        ],
    )
    await service.create_challenge(payload)

    with pytest.raises(DuplicateOrderIndexError):
        await service.create_challenge(payload)


@pytest.mark.asyncio
async def test_create_challenge_option_rejects_second_correct_option_on_select() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.SELECT,
            question="Q1",
            order_index=1,
            options=[
                ChallengeOptionCreate(text="A", correct=True, order_index=1),
                ChallengeOptionCreate(text="B", correct=False, order_index=2),
            ],
        )
    )

    with pytest.raises(InvalidChallengeOptionsError):
        await service.create_challenge_option(
            challenge.id, ChallengeOptionCreate(text="C", correct=True, order_index=3)
        )


@pytest.mark.asyncio
async def test_delete_challenge_option_rejects_dropping_below_two_options() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.SELECT,
            question="Q1",
            order_index=1,
            options=[
                ChallengeOptionCreate(text="A", correct=True, order_index=1),
                ChallengeOptionCreate(text="B", correct=False, order_index=2),
            ],
        )
    )
    option_to_delete = challenge.options[0]

    with pytest.raises(InvalidChallengeOptionsError):
        await service.delete_challenge_option(option_to_delete.id)


@pytest.mark.asyncio
async def test_update_challenge_option_rejects_removing_only_correct_answer() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.SELECT,
            question="Q1",
            order_index=1,
            options=[
                ChallengeOptionCreate(text="A", correct=True, order_index=1),
                ChallengeOptionCreate(text="B", correct=False, order_index=2),
            ],
        )
    )
    correct_option = next(o for o in challenge.options if o.correct)

    with pytest.raises(InvalidChallengeOptionsError):
        await service.update_challenge_option(
            correct_option.id, ChallengeOptionUpdate(correct=False)
        )


@pytest.mark.asyncio
async def test_update_challenge_type_rejects_multiple_correct_switching_to_select() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.ASSIST,
            question="Q1",
            order_index=1,
            options=[
                ChallengeOptionCreate(text="A", correct=True, order_index=1),
                ChallengeOptionCreate(text="B", correct=True, order_index=2),
            ],
        )
    )

    with pytest.raises(InvalidChallengeOptionsError):
        await service.update_challenge(challenge.id, ChallengeUpdate(type=ChallengeType.SELECT))


@pytest.mark.asyncio
async def test_get_course_not_found() -> None:
    service, _, _, _, _, _ = build_service()

    with pytest.raises(CourseNotFoundError):
        await service.get_course("missing")


@pytest.mark.asyncio
async def test_create_unit_requires_existing_course() -> None:
    service, _, _, _, _, _ = build_service()

    with pytest.raises(CourseNotFoundError):
        await service.create_unit(
            UnitCreate(course_id="missing", title="Unit", description="d", order_index=1)
        )


@pytest.mark.asyncio
async def test_update_course_partial_fields_only() -> None:
    service, courses, _, _, _, _ = build_service()
    await courses.create(Course(id="course-1", title="Spanish", image_src="/es.svg"))

    updated = await service.update_course("course-1", CourseUpdate(title="Español"))

    assert updated.title == "Español"
    assert updated.image_src == "/es.svg"


@pytest.mark.asyncio
async def test_get_challenge_not_found() -> None:
    service, _, _, _, _, _ = build_service()

    with pytest.raises(ChallengeNotFoundError):
        await service.get_challenge("missing")


@pytest.mark.asyncio
async def test_create_challenge_persists_explanation() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)

    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.SELECT,
            question="What is the capital of France?",
            explanation="Paris is the capital and largest city of France.",
            order_index=1,
            options=[
                ChallengeOptionCreate(text="Paris", correct=True, order_index=1),
                ChallengeOptionCreate(text="London", correct=False, order_index=2),
            ],
        )
    )

    assert challenge.explanation == "Paris is the capital and largest city of France."
    assert challenge.difficulty == ChallengeDifficulty.EASY


@pytest.mark.asyncio
async def test_create_challenge_persists_explicit_difficulty() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)

    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.SELECT,
            question="Q1",
            difficulty=ChallengeDifficulty.HARD,
            order_index=1,
            options=[
                ChallengeOptionCreate(text="A", correct=True, order_index=1),
                ChallengeOptionCreate(text="B", correct=False, order_index=2),
            ],
        )
    )

    assert challenge.difficulty == ChallengeDifficulty.HARD


@pytest.mark.asyncio
async def test_create_challenge_rejects_duplicate_option_order_index() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)

    payload = ChallengeCreate(
        lesson_id=lesson_id,
        type=ChallengeType.SELECT,
        question="Q1",
        order_index=1,
        options=[
            ChallengeOptionCreate(text="A", correct=True, order_index=1),
            ChallengeOptionCreate(text="B", correct=False, order_index=1),
        ],
    )

    with pytest.raises(DuplicateOrderIndexError):
        await service.create_challenge(payload)


@pytest.mark.asyncio
async def test_create_challenge_option_rejects_duplicate_order_index() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.ASSIST,
            question="Q1",
            order_index=1,
            options=[
                ChallengeOptionCreate(text="A", correct=True, order_index=1),
                ChallengeOptionCreate(text="B", correct=False, order_index=2),
            ],
        )
    )

    with pytest.raises(DuplicateOrderIndexError):
        await service.create_challenge_option(
            challenge.id, ChallengeOptionCreate(text="C", correct=False, order_index=2)
        )


@pytest.mark.asyncio
async def test_update_challenge_option_rejects_duplicate_order_index() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.SELECT,
            question="Q1",
            order_index=1,
            options=[
                ChallengeOptionCreate(text="A", correct=True, order_index=1),
                ChallengeOptionCreate(text="B", correct=False, order_index=2),
            ],
        )
    )
    option_b = next(o for o in challenge.options if o.text == "B")

    with pytest.raises(DuplicateOrderIndexError):
        await service.update_challenge_option(
            option_b.id, ChallengeOptionUpdate(order_index=1)
        )


@pytest.mark.asyncio
async def test_create_topic_rejects_duplicate_name() -> None:
    service, _, _, _, _, _ = build_service()
    await service.create_topic(TopicCreate(name="Grammar"))

    with pytest.raises(DuplicateTopicNameError):
        await service.create_topic(TopicCreate(name="Grammar"))


@pytest.mark.asyncio
async def test_update_topic_rejects_renaming_to_existing_name() -> None:
    service, _, _, _, _, _ = build_service()
    await service.create_topic(TopicCreate(name="Grammar"))
    vocabulary = await service.create_topic(TopicCreate(name="Vocabulary"))

    with pytest.raises(DuplicateTopicNameError):
        await service.update_topic(vocabulary.id, TopicUpdate(name="Grammar"))


@pytest.mark.asyncio
async def test_update_topic_allows_keeping_its_own_name() -> None:
    service, _, _, _, _, _ = build_service()
    topic = await service.create_topic(TopicCreate(name="Grammar"))

    updated = await service.update_topic(topic.id, TopicUpdate(name="Grammar"))

    assert updated.name == "Grammar"


@pytest.mark.asyncio
async def test_create_challenge_rejects_unknown_topic() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)

    payload = ChallengeCreate(
        lesson_id=lesson_id,
        type=ChallengeType.SELECT,
        question="Q1",
        topic_id="missing-topic",
        order_index=1,
        options=[
            ChallengeOptionCreate(text="A", correct=True, order_index=1),
            ChallengeOptionCreate(text="B", correct=False, order_index=2),
        ],
    )

    with pytest.raises(TopicNotFoundError):
        await service.create_challenge(payload)


@pytest.mark.asyncio
async def test_create_challenge_persists_topic() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    topic = await service.create_topic(TopicCreate(name="Grammar"))

    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.SELECT,
            question="Q1",
            topic_id=topic.id,
            order_index=1,
            options=[
                ChallengeOptionCreate(text="A", correct=True, order_index=1),
                ChallengeOptionCreate(text="B", correct=False, order_index=2),
            ],
        )
    )

    assert challenge.topic_id == topic.id


@pytest.mark.asyncio
async def test_update_challenge_rejects_unknown_topic() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    challenge = await service.create_challenge(
        ChallengeCreate(
            lesson_id=lesson_id,
            type=ChallengeType.SELECT,
            question="Q1",
            order_index=1,
            options=[
                ChallengeOptionCreate(text="A", correct=True, order_index=1),
                ChallengeOptionCreate(text="B", correct=False, order_index=2),
            ],
        )
    )

    with pytest.raises(TopicNotFoundError):
        await service.update_challenge(challenge.id, ChallengeUpdate(topic_id="missing-topic"))


@pytest.mark.asyncio
async def test_list_challenges_by_topic_requires_existing_topic() -> None:
    service, _, _, _, _, _ = build_service()

    with pytest.raises(TopicNotFoundError):
        await service.list_challenges_by_topic("missing-topic")


@pytest.mark.asyncio
async def test_list_challenges_by_topic_filters_other_topics() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    grammar = await service.create_topic(TopicCreate(name="Grammar"))
    vocabulary = await service.create_topic(TopicCreate(name="Vocabulary"))

    async def make_challenge(order_index: int, topic_id: str) -> Challenge:
        return await service.create_challenge(
            ChallengeCreate(
                lesson_id=lesson_id,
                type=ChallengeType.SELECT,
                question=f"Q{order_index}",
                topic_id=topic_id,
                order_index=order_index,
                options=[
                    ChallengeOptionCreate(text="A", correct=True, order_index=1),
                    ChallengeOptionCreate(text="B", correct=False, order_index=2),
                ],
            )
        )

    grammar_challenge = await make_challenge(1, grammar.id)
    await make_challenge(2, vocabulary.id)

    result = await service.list_challenges_by_topic(grammar.id)

    assert [c.id for c in result] == [grammar_challenge.id]


@pytest.mark.asyncio
async def test_get_topic_stats_groups_by_topic_and_difficulty() -> None:
    service, _, units, _, _, _ = build_service()
    lesson_id = await _seed_lesson(service, units)
    grammar = await service.create_topic(TopicCreate(name="Grammar"))

    async def make_challenge(
        order_index: int, topic_id: str | None, difficulty: ChallengeDifficulty
    ) -> Challenge:
        return await service.create_challenge(
            ChallengeCreate(
                lesson_id=lesson_id,
                type=ChallengeType.SELECT,
                question=f"Q{order_index}",
                topic_id=topic_id,
                difficulty=difficulty,
                order_index=order_index,
                options=[
                    ChallengeOptionCreate(text="A", correct=True, order_index=1),
                    ChallengeOptionCreate(text="B", correct=False, order_index=2),
                ],
            )
        )

    await make_challenge(1, grammar.id, ChallengeDifficulty.EASY)
    await make_challenge(2, grammar.id, ChallengeDifficulty.HARD)
    await make_challenge(3, None, ChallengeDifficulty.MEDIUM)

    stats = await service.get_topic_stats()
    by_name = {stat.topic_name: stat for stat in stats}

    assert by_name["Grammar"].total == 2
    assert by_name["Grammar"].by_difficulty == {"EASY": 1, "MEDIUM": 0, "HARD": 1}
    assert by_name["Chưa phân loại"].total == 1
    assert by_name["Chưa phân loại"].by_difficulty == {"EASY": 0, "MEDIUM": 1, "HARD": 0}
