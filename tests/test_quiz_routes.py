from typing import NoReturn

import pytest
from fastapi import HTTPException, status

from app.api.routes.challenges import check_answer
from app.api.routes.lessons import generate_quiz
from app.api.routes.units import generate_stage_quizzes
from app.core.exceptions import ChallengeNotFoundError, LessonNotFoundError, UnitNotFoundError
from app.schemas.quiz import (
    AnswerCheckRequest,
    AnswerCheckResult,
    QuizGenerateRequest,
    QuizSet,
    StageQuizSet,
)


class FailingQuizService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate_for_lesson(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def generate_for_unit(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def check_answer(self, *_: object, **__: object) -> NoReturn:
        raise self.error


class FakeQuizService:
    def __init__(
        self,
        quiz_set: QuizSet | None = None,
        stage_sets: list[StageQuizSet] | None = None,
        answer_result: AnswerCheckResult | None = None,
    ) -> None:
        self.quiz_set = quiz_set
        self.stage_sets = stage_sets or []
        self.answer_result = answer_result

    async def generate_for_lesson(self, lesson_id: str, **_: object) -> QuizSet:
        assert self.quiz_set is not None
        return self.quiz_set

    async def generate_for_unit(self, unit_id: str, **_: object) -> list[StageQuizSet]:
        return self.stage_sets

    async def check_answer(self, challenge_id: str, selected_option_id: str) -> AnswerCheckResult:
        assert self.answer_result is not None
        return self.answer_result


@pytest.mark.asyncio
async def test_generate_quiz_maps_lesson_not_found_to_404() -> None:
    service = FailingQuizService(LessonNotFoundError("missing"))

    with pytest.raises(HTTPException) as raised:
        await generate_quiz("missing", QuizGenerateRequest(), service)  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_generate_quiz_returns_service_result() -> None:
    expected = QuizSet(lesson_id="lesson-1", requested_count=10, returned_count=0, questions=[])
    service = FakeQuizService(quiz_set=expected)

    result = await generate_quiz("lesson-1", QuizGenerateRequest(), service)  # type: ignore[arg-type]

    assert result is expected


@pytest.mark.asyncio
async def test_generate_stage_quizzes_maps_unit_not_found_to_404() -> None:
    service = FailingQuizService(UnitNotFoundError("missing"))

    with pytest.raises(HTTPException) as raised:
        await generate_stage_quizzes("missing", QuizGenerateRequest(), service)  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_generate_stage_quizzes_returns_service_result() -> None:
    expected = [
        StageQuizSet(
            lesson_id="lesson-1",
            lesson_title="Stage 1",
            requested_count=10,
            returned_count=0,
            questions=[],
        )
    ]
    service = FakeQuizService(stage_sets=expected)

    result = await generate_stage_quizzes("unit-1", QuizGenerateRequest(), service)  # type: ignore[arg-type]

    assert result == expected


@pytest.mark.asyncio
async def test_check_answer_maps_challenge_not_found_to_404() -> None:
    service = FailingQuizService(ChallengeNotFoundError("missing"))

    with pytest.raises(HTTPException) as raised:
        await check_answer(
            "missing", AnswerCheckRequest(selected_option_id="opt-1"), service  # type: ignore[arg-type]
        )

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_check_answer_returns_service_result() -> None:
    expected = AnswerCheckResult(
        challenge_id="c0",
        selected_option_id="c0-a",
        correct=True,
        correct_option_ids=["c0-a"],
        explanation=None,
    )
    service = FakeQuizService(answer_result=expected)

    result = await check_answer(
        "c0", AnswerCheckRequest(selected_option_id="c0-a"), service  # type: ignore[arg-type]
    )

    assert result is expected
