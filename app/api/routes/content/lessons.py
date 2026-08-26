from typing import Annotated

from fastapi import APIRouter, Query

from app.api.dependencies import CourseContentServiceDependency, QuizServiceDependency
from app.api.routes.content._content_errors import raise_content_http_error
from app.core.exceptions import ApplicationError
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.content.quiz import QuizGenerateRequest, QuizSet

router = APIRouter()

MAX_CHALLENGES_PER_PAGE = 100


@router.get("/{lesson_id}/challenges", response_model=list[ChallengePublicRead])
async def list_challenges(
    lesson_id: str,
    service: CourseContentServiceDependency,
    limit: Annotated[int, Query(gt=0, le=MAX_CHALLENGES_PER_PAGE)] = MAX_CHALLENGES_PER_PAGE,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[ChallengePublicRead]:
    """Always paged. Path lessons are small enough that the default page covers
    them whole, but bank lessons hold tens of thousands of challenges and must
    never be serialised in one response."""
    challenges = await service.list_challenges(lesson_id, limit=limit, offset=offset)
    return [ChallengePublicRead.model_validate(challenge) for challenge in challenges]


@router.post("/{lesson_id}/quiz", response_model=QuizSet)
async def generate_quiz(
    lesson_id: str, data: QuizGenerateRequest, service: QuizServiceDependency
) -> QuizSet:
    try:
        return await service.generate_for_lesson(
            lesson_id,
            topic_ids=data.topic_ids,
            difficulties=data.difficulties,
            count=data.count,
            exclude_ids=data.exclude_ids,
            seed=data.seed,
        )
    except ApplicationError as exc:
        raise_content_http_error(exc)
