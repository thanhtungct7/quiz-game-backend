from fastapi import APIRouter

from app.api.dependencies import (
    BenchmarkExamServiceDependency,
    CourseContentServiceDependency,
    CurrentUser,
    ProgressServiceDependency,
)
from app.api.routes.content._content_errors import raise_content_http_error
from app.core.exceptions import ApplicationError, ChallengeLockedByExamError
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
    exams: BenchmarkExamServiceDependency,
) -> AnswerCheckResult:
    """Grade one study answer and reveal the solution.

    Refused for a challenge on the caller's open Benchmark Exam paper: the
    result carries the correct options, so answering it here first would be
    a way to look the answer up before sitting it.
    """
    try:
        if await exams.is_challenge_locked(current_user.id, challenge_id):
            raise ChallengeLockedByExamError(
                "This question is on your open Benchmark Exam paper"
            )
        return await service.check_answer(
            current_user.id,
            challenge_id,
            data.selected_option_id,
            selected_option_ids=data.selected_option_ids,
        )
    except ApplicationError as exc:
        raise_content_http_error(exc)
