from fastapi import APIRouter

from app.api.dependencies import CourseContentServiceDependency
from app.api.routes._content_errors import raise_content_http_error
from app.core.exceptions import ApplicationError
from app.schemas.course_content import ChallengePublicRead

router = APIRouter()


@router.get("/{challenge_id}", response_model=ChallengePublicRead)
async def get_challenge(
    challenge_id: str, service: CourseContentServiceDependency
) -> ChallengePublicRead:
    try:
        challenge = await service.get_challenge(challenge_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return ChallengePublicRead.model_validate(challenge)
