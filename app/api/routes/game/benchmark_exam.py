from typing import Annotated

from fastapi import APIRouter, Query, status

from app.api.dependencies import BenchmarkExamServiceDependency, CurrentUser
from app.api.rate_limit import limit_by_user
from app.api.routes.game._game_errors import raise_game_http_error
from app.core import rate_limit_policies as limits
from app.core.exceptions import ApplicationError
from app.schemas.game.benchmark_exam import (
    BenchmarkAnswerAck,
    BenchmarkAnswerRequest,
    BenchmarkAttemptRead,
    BenchmarkAttemptStartRequest,
    BenchmarkAttemptSummary,
    BenchmarkResultRead,
)

router = APIRouter()


@router.post(
    "/attempts",
    response_model=BenchmarkAttemptRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[limit_by_user("exam-start", limits.EXAM_START_PER_USER)],
)
async def start_attempt(
    payload: BenchmarkAttemptStartRequest,
    current_user: CurrentUser,
    service: BenchmarkExamServiceDependency,
) -> BenchmarkAttemptRead:
    """Draw a paper for one cap and open a sitting on it."""
    try:
        return await service.start(current_user.id, payload.cap_level)
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.post(
    "/attempts/{attempt_id}/answers",
    response_model=BenchmarkAnswerAck,
    dependencies=[limit_by_user("exam-answer", limits.EXAM_ANSWER_PER_USER)],
)
async def answer_question(
    attempt_id: str,
    payload: BenchmarkAnswerRequest,
    current_user: CurrentUser,
    service: BenchmarkExamServiceDependency,
) -> BenchmarkAnswerAck:
    """Answer one question on the paper. The response never says whether it was right."""
    try:
        return await service.answer(
            current_user.id,
            attempt_id,
            payload.challenge_id,
            payload.selected_option_id,
            payload.selected_option_ids,
        )
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.post("/attempts/{attempt_id}/submit", response_model=BenchmarkResultRead)
async def submit_attempt(
    attempt_id: str,
    current_user: CurrentUser,
    service: BenchmarkExamServiceDependency,
) -> BenchmarkResultRead:
    """Hand the paper in and get the grade. Safe to retry."""
    try:
        return await service.submit(current_user.id, attempt_id)
    except ApplicationError as exc:
        raise_game_http_error(exc)


@router.get("/attempts", response_model=list[BenchmarkAttemptSummary])
async def list_attempts(
    current_user: CurrentUser,
    service: BenchmarkExamServiceDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
) -> list[BenchmarkAttemptSummary]:
    return await service.history(current_user.id, limit)
