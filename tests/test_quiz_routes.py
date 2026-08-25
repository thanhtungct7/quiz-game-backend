from typing import NoReturn

import pytest
from fastapi import HTTPException, status

from app.api.routes.content.lessons import generate_quiz
from app.api.routes.content.units import generate_stage_quizzes
from app.core.exceptions import LessonNotFoundError, UnitNotFoundError
from app.schemas.content.quiz import QuizGenerateRequest, QuizSet, StageQuizSet


class FailingQuizService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def generate_for_lesson(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def generate_for_unit(self, *_: object, **__: object) -> NoReturn:
        raise self.error


class FakeQuizService:
    def __init__(
        self,
        quiz_set: QuizSet | None = None,
        stage_sets: list[StageQuizSet] | None = None,
    ) -> None:
        self.quiz_set = quiz_set
        self.stage_sets = stage_sets or []

    async def generate_for_lesson(self, lesson_id: str, **_: object) -> QuizSet:
        assert self.quiz_set is not None
        return self.quiz_set

    async def generate_for_unit(self, unit_id: str, **_: object) -> list[StageQuizSet]:
        return self.stage_sets


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
