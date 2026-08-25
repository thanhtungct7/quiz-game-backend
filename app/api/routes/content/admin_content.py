"""Admin-only CRUD for the question bank: courses, units, lessons, challenges,
challenge options and topics. Every route requires an authenticated admin user.
"""

from fastapi import APIRouter, Response, status

from app.api.dependencies import AdminUser, CourseContentServiceDependency
from app.api.routes._content_errors import raise_content_http_error
from app.core.exceptions import ApplicationError
from app.schemas.course_content import (
    ChallengeCreate,
    ChallengeOptionCreate,
    ChallengeOptionRead,
    ChallengeOptionUpdate,
    ChallengeRead,
    ChallengeUpdate,
    CourseCreate,
    CourseRead,
    CourseUpdate,
    LessonCreate,
    LessonRead,
    LessonUpdate,
    TopicCreate,
    TopicRead,
    TopicStats,
    TopicUpdate,
    UnitCreate,
    UnitRead,
    UnitUpdate,
)

courses_router = APIRouter()
units_router = APIRouter()
lessons_router = APIRouter()
challenges_router = APIRouter()
challenge_options_router = APIRouter()
topics_router = APIRouter()

# --- Courses -----------------------------------------------------------------


@courses_router.post("", response_model=CourseRead, status_code=status.HTTP_201_CREATED)
async def create_course(
    payload: CourseCreate, _: AdminUser, service: CourseContentServiceDependency
) -> CourseRead:
    course = await service.create_course(payload)
    return CourseRead.model_validate(course)


@courses_router.get("", response_model=list[CourseRead])
async def list_courses(_: AdminUser, service: CourseContentServiceDependency) -> list[CourseRead]:
    courses = await service.list_courses()
    return [CourseRead.model_validate(course) for course in courses]


@courses_router.get("/{course_id}", response_model=CourseRead)
async def get_course(
    course_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> CourseRead:
    try:
        course = await service.get_course(course_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return CourseRead.model_validate(course)


@courses_router.patch("/{course_id}", response_model=CourseRead)
async def update_course(
    course_id: str,
    payload: CourseUpdate,
    _: AdminUser,
    service: CourseContentServiceDependency,
) -> CourseRead:
    try:
        course = await service.update_course(course_id, payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return CourseRead.model_validate(course)


@courses_router.delete("/{course_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_course(
    course_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> Response:
    try:
        await service.delete_course(course_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Units ---------------------------------------------------------------------


@units_router.post("", response_model=UnitRead, status_code=status.HTTP_201_CREATED)
async def create_unit(
    payload: UnitCreate, _: AdminUser, service: CourseContentServiceDependency
) -> UnitRead:
    try:
        unit = await service.create_unit(payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return UnitRead.model_validate(unit)


@units_router.get("/{unit_id}", response_model=UnitRead)
async def get_unit(
    unit_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> UnitRead:
    try:
        unit = await service.get_unit(unit_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return UnitRead.model_validate(unit)


@units_router.patch("/{unit_id}", response_model=UnitRead)
async def update_unit(
    unit_id: str,
    payload: UnitUpdate,
    _: AdminUser,
    service: CourseContentServiceDependency,
) -> UnitRead:
    try:
        unit = await service.update_unit(unit_id, payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return UnitRead.model_validate(unit)


@units_router.delete("/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_unit(
    unit_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> Response:
    try:
        await service.delete_unit(unit_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Lessons -------------------------------------------------------------------


@lessons_router.post("", response_model=LessonRead, status_code=status.HTTP_201_CREATED)
async def create_lesson(
    payload: LessonCreate, _: AdminUser, service: CourseContentServiceDependency
) -> LessonRead:
    try:
        lesson = await service.create_lesson(payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return LessonRead.model_validate(lesson)


@lessons_router.get("/{lesson_id}", response_model=LessonRead)
async def get_lesson(
    lesson_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> LessonRead:
    try:
        lesson = await service.get_lesson(lesson_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return LessonRead.model_validate(lesson)


@lessons_router.patch("/{lesson_id}", response_model=LessonRead)
async def update_lesson(
    lesson_id: str,
    payload: LessonUpdate,
    _: AdminUser,
    service: CourseContentServiceDependency,
) -> LessonRead:
    try:
        lesson = await service.update_lesson(lesson_id, payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return LessonRead.model_validate(lesson)


@lessons_router.delete("/{lesson_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_lesson(
    lesson_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> Response:
    try:
        await service.delete_lesson(lesson_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Challenges ----------------------------------------------------------------


@challenges_router.post("", response_model=ChallengeRead, status_code=status.HTTP_201_CREATED)
async def create_challenge(
    payload: ChallengeCreate, _: AdminUser, service: CourseContentServiceDependency
) -> ChallengeRead:
    try:
        challenge = await service.create_challenge(payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return ChallengeRead.model_validate(challenge)


@challenges_router.get("/{challenge_id}", response_model=ChallengeRead)
async def get_challenge(
    challenge_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> ChallengeRead:
    try:
        challenge = await service.get_challenge(challenge_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return ChallengeRead.model_validate(challenge)


@challenges_router.patch("/{challenge_id}", response_model=ChallengeRead)
async def update_challenge(
    challenge_id: str,
    payload: ChallengeUpdate,
    _: AdminUser,
    service: CourseContentServiceDependency,
) -> ChallengeRead:
    try:
        challenge = await service.update_challenge(challenge_id, payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return ChallengeRead.model_validate(challenge)


@challenges_router.delete("/{challenge_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_challenge(
    challenge_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> Response:
    try:
        await service.delete_challenge(challenge_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Challenge options -----------------------------------------------------------


@challenge_options_router.post(
    "/challenges/{challenge_id}/options",
    response_model=ChallengeOptionRead,
    status_code=status.HTTP_201_CREATED,
)
async def create_challenge_option(
    challenge_id: str,
    payload: ChallengeOptionCreate,
    _: AdminUser,
    service: CourseContentServiceDependency,
) -> ChallengeOptionRead:
    try:
        option = await service.create_challenge_option(challenge_id, payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return ChallengeOptionRead.model_validate(option)


@challenge_options_router.get(
    "/challenges/{challenge_id}/options", response_model=list[ChallengeOptionRead]
)
async def list_challenge_options(
    challenge_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> list[ChallengeOptionRead]:
    try:
        options = await service.list_challenge_options(challenge_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return [ChallengeOptionRead.model_validate(option) for option in options]


@challenge_options_router.get("/options/{option_id}", response_model=ChallengeOptionRead)
async def get_challenge_option(
    option_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> ChallengeOptionRead:
    try:
        option = await service.get_challenge_option(option_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return ChallengeOptionRead.model_validate(option)


@challenge_options_router.patch("/options/{option_id}", response_model=ChallengeOptionRead)
async def update_challenge_option(
    option_id: str,
    payload: ChallengeOptionUpdate,
    _: AdminUser,
    service: CourseContentServiceDependency,
) -> ChallengeOptionRead:
    try:
        option = await service.update_challenge_option(option_id, payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return ChallengeOptionRead.model_validate(option)


@challenge_options_router.delete("/options/{option_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_challenge_option(
    option_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> Response:
    try:
        await service.delete_challenge_option(option_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Topics ----------------------------------------------------------------
#
# Topics let questions be filtered and counted independently of the course /
# unit / lesson hierarchy (e.g. "Grammar", "Vocabulary"). `topic_id` on a
# challenge is optional, so untagged challenges keep working unchanged.


@topics_router.post("", response_model=TopicRead, status_code=status.HTTP_201_CREATED)
async def create_topic(
    payload: TopicCreate, _: AdminUser, service: CourseContentServiceDependency
) -> TopicRead:
    try:
        topic = await service.create_topic(payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return TopicRead.model_validate(topic)


@topics_router.get("", response_model=list[TopicRead])
async def list_topics(_: AdminUser, service: CourseContentServiceDependency) -> list[TopicRead]:
    topics = await service.list_topics()
    return [TopicRead.model_validate(topic) for topic in topics]


# Registered before "/{topic_id}" so "stats" is never matched as a topic id.
@topics_router.get("/stats", response_model=list[TopicStats])
async def get_topic_stats(
    _: AdminUser, service: CourseContentServiceDependency
) -> list[TopicStats]:
    return await service.get_topic_stats()


@topics_router.get("/{topic_id}", response_model=TopicRead)
async def get_topic(
    topic_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> TopicRead:
    try:
        topic = await service.get_topic(topic_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return TopicRead.model_validate(topic)


@topics_router.patch("/{topic_id}", response_model=TopicRead)
async def update_topic(
    topic_id: str,
    payload: TopicUpdate,
    _: AdminUser,
    service: CourseContentServiceDependency,
) -> TopicRead:
    try:
        topic = await service.update_topic(topic_id, payload)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return TopicRead.model_validate(topic)


@topics_router.delete("/{topic_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_topic(
    topic_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> Response:
    try:
        await service.delete_topic(topic_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@topics_router.get("/{topic_id}/challenges", response_model=list[ChallengeRead])
async def list_topic_challenges(
    topic_id: str, _: AdminUser, service: CourseContentServiceDependency
) -> list[ChallengeRead]:
    try:
        challenges = await service.list_challenges_by_topic(topic_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return [ChallengeRead.model_validate(challenge) for challenge in challenges]
