from fastapi import APIRouter

from app.api.dependencies import CourseContentServiceDependency
from app.schemas.course_content import ChallengePublicRead

router = APIRouter()


@router.get("/{lesson_id}/challenges", response_model=list[ChallengePublicRead])
async def list_challenges(
    lesson_id: str, service: CourseContentServiceDependency
) -> list[ChallengePublicRead]:
    challenges = await service.list_challenges(lesson_id)
    return [ChallengePublicRead.model_validate(challenge) for challenge in challenges]
