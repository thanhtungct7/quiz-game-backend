from fastapi import APIRouter

from app.api.dependencies import CourseContentServiceDependency, QuizServiceDependency
from app.api.routes.content._content_errors import raise_content_http_error
from app.core.exceptions import ApplicationError
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.content.quiz import QuizGenerateRequest, QuizSet

router = APIRouter()


@router.get("/{lesson_id}/challenges", response_model=list[ChallengePublicRead])
async def list_challenges(
    lesson_id: str, service: CourseContentServiceDependency
) -> list[ChallengePublicRead]:
    challenges = await service.list_challenges(lesson_id)
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
