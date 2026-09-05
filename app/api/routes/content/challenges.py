from fastapi import APIRouter

from app.api.dependencies import (
    CourseContentServiceDependency,
    CurrentUser,
    ProgressServiceDependency,
)
from app.api.routes.content._content_errors import raise_content_http_error
from app.core.exceptions import ApplicationError
from app.schemas.content.course_content import ChallengePublicRead
from app.schemas.content.quiz import AnswerCheckRequest, AnswerCheckResult
from app.services.content.challenge_presenter import to_public_challenge

router = APIRouter()


@router.get("/{challenge_id}", response_model=ChallengePublicRead)
async def get_challenge(
    challenge_id: str, service: CourseContentServiceDependency
) -> ChallengePublicRead:
    try:
        challenge = await service.get_challenge(challenge_id)
    except ApplicationError as exc:
        raise_content_http_error(exc)
    return to_public_challenge(challenge)


@router.post("/{challenge_id}/check", response_model=AnswerCheckResult)
async def check_answer(
    challenge_id: str,
    data: AnswerCheckRequest,
    current_user: CurrentUser,
    service: ProgressServiceDependency,
) -> AnswerCheckResult:
    try:
        return await service.check_answer(
            current_user.id,
            challenge_id,
            data.selected_option_id,
            selected_option_ids=data.selected_option_ids,
        )
    except ApplicationError as exc:
        raise_content_http_error(exc)
