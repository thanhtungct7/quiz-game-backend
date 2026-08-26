from typing import NoReturn

import pytest
from fastapi import HTTPException, status

from app.api.routes.content.challenges import check_answer
from app.api.routes.progress.progress import (
    get_course_progress,
    get_lesson_progress,
    get_unit_progress,
)
from app.core.exceptions import (
    ChallengeNotFoundError,
    CourseNotFoundError,
    LessonNotFoundError,
    UnitNotFoundError,
)
from app.models.auth.user import User
from app.models.progress.user_lesson_progress import LessonProgressStatus
from app.schemas.content.quiz import AnswerCheckRequest, AnswerCheckResult
from app.schemas.progress.progress import (
    CourseProgressRead,
    LessonProgressRead,
    UnitProgressRead,
)


def _make_user() -> User:
    return User(id="user-1", email="user@example.com")


class FailingProgressService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def check_answer(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def get_lesson_progress(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def get_unit_progress(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def get_course_progress(self, *_: object, **__: object) -> NoReturn:
        raise self.error


class FakeProgressService:
    def __init__(
        self,
        answer_result: AnswerCheckResult | None = None,
        lesson_progress: LessonProgressRead | None = None,
        unit_progress: UnitProgressRead | None = None,
        course_progress: CourseProgressRead | None = None,
    ) -> None:
        self.answer_result = answer_result
        self.lesson_progress = lesson_progress
        self.unit_progress = unit_progress
        self.course_progress = course_progress

    async def check_answer(
        self, user_id: str, challenge_id: str, selected_option_id: str
    ) -> AnswerCheckResult:
        assert self.answer_result is not None
        return self.answer_result

    async def get_lesson_progress(self, user_id: str, lesson_id: str) -> LessonProgressRead:
        assert self.lesson_progress is not None
        return self.lesson_progress

    async def get_unit_progress(self, user_id: str, unit_id: str) -> UnitProgressRead:
        assert self.unit_progress is not None
        return self.unit_progress

    async def get_course_progress(self, user_id: str, course_id: str) -> CourseProgressRead:
        assert self.course_progress is not None
        return self.course_progress


@pytest.mark.asyncio
async def test_check_answer_maps_challenge_not_found_to_404() -> None:
    service = FailingProgressService(ChallengeNotFoundError("missing"))

    with pytest.raises(HTTPException) as raised:
        await check_answer(
            "missing",
            AnswerCheckRequest(selected_option_id="opt-1"),
            _make_user(),  # type: ignore[arg-type]
            service,  # type: ignore[arg-type]
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
    service = FakeProgressService(answer_result=expected)

    result = await check_answer(
        "c0",
        AnswerCheckRequest(selected_option_id="c0-a"),
        _make_user(),  # type: ignore[arg-type]
        service,  # type: ignore[arg-type]
    )

    assert result is expected


@pytest.mark.asyncio
async def test_get_lesson_progress_maps_lesson_not_found_to_404() -> None:
    service = FailingProgressService(LessonNotFoundError("missing"))

    with pytest.raises(HTTPException) as raised:
        await get_lesson_progress("missing", _make_user(), service)  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_lesson_progress_returns_service_result() -> None:
    expected = LessonProgressRead(
        lesson_id="lesson-1",
        status=LessonProgressStatus.IN_PROGRESS,
        correct_challenge_count=1,
        total_challenge_count=2,
        completed_at=None,
    )
    service = FakeProgressService(lesson_progress=expected)

    result = await get_lesson_progress("lesson-1", _make_user(), service)  # type: ignore[arg-type]

    assert result is expected


@pytest.mark.asyncio
async def test_get_unit_progress_maps_unit_not_found_to_404() -> None:
    service = FailingProgressService(UnitNotFoundError("missing"))

    with pytest.raises(HTTPException) as raised:
        await get_unit_progress("missing", _make_user(), service)  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_unit_progress_returns_service_result() -> None:
    expected = UnitProgressRead(unit_id="unit-1", lessons=[])
    service = FakeProgressService(unit_progress=expected)

    result = await get_unit_progress("unit-1", _make_user(), service)  # type: ignore[arg-type]

    assert result is expected


@pytest.mark.asyncio
async def test_get_course_progress_maps_course_not_found_to_404() -> None:
    service = FailingProgressService(CourseNotFoundError("missing"))

    with pytest.raises(HTTPException) as raised:
        await get_course_progress("missing", _make_user(), service)  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_get_course_progress_returns_service_result() -> None:
    expected = CourseProgressRead(course_id="course-1", lessons=[])
    service = FakeProgressService(course_progress=expected)

    result = await get_course_progress("course-1", _make_user(), service)  # type: ignore[arg-type]

    assert result is expected
