from fastapi import APIRouter

from app.api.dependencies import CourseContentServiceDependency, QuizServiceDependency
from app.api.routes._content_errors import raise_content_http_error
from app.core.exceptions import ApplicationError
from app.schemas.course_content import ChallengePublicRead
from app.schemas.quiz import AnswerCheckRequest, AnswerCheckResult

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


@router.post("/{challenge_id}/check", response_model=AnswerCheckResult)
async def check_answer(
    challenge_id: str, data: AnswerCheckRequest, service: QuizServiceDependency
) -> AnswerCheckResult:
    try:
        return await service.check_answer(challenge_id, data.selected_option_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
