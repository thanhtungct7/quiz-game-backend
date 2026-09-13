from datetime import UTC, datetime
from typing import NoReturn

import httpx

from app.api.dependencies import get_benchmark_exam_service, get_current_user
from app.core.exceptions import (
    BenchmarkExamAlreadyClearedError,
    BenchmarkExamAttemptClosedError,
    BenchmarkExamAttemptNotFoundError,
    BenchmarkExamNotEligibleError,
    BenchmarkExamNotEnoughQuestionsError,
    BenchmarkExamQuestionAlreadyAnsweredError,
    BenchmarkExamQuestionNotInAttemptError,
    ChallengeOptionNotFoundError,
    InvalidAnswerSubmissionError,
)
from app.main import app
from app.models.auth.user import User
from app.schemas.game.benchmark_exam import BenchmarkAnswerAck, BenchmarkAttemptRead

NOW = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)


class StubService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    def _raise(self) -> None:
        if self.error is not None:
            raise self.error

    async def start(self, user_id: str, cap_level: int) -> BenchmarkAttemptRead:
        self.calls.append(("start", user_id, cap_level))
        self._raise()
        return BenchmarkAttemptRead(
            attempt_id="a1",
            cap_level=cap_level,
            questions=[],
            total=30,
            pass_percent=80,
            started_at=NOW,
            expires_at=NOW,
        )

    async def answer(self, *args: object) -> BenchmarkAnswerAck:
        self.calls.append(("answer", *args))
        self._raise()
        return BenchmarkAnswerAck(attempt_id="a1", answered_count=1, total=30)

    async def submit(self, *_: object) -> NoReturn:
        self._raise()
        raise AssertionError("not configured")

    async def history(self, user_id: str, limit: int) -> list[object]:
        self.calls.append(("history", user_id, limit))
        return []


async def _request(
    service: StubService, method: str, path: str, json: dict[str, object] | None = None
) -> httpx.Response:
    app.dependency_overrides[get_current_user] = lambda: User(id="user-1", email="u@example.com")
    app.dependency_overrides[get_benchmark_exam_service] = lambda: service
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, f"/api/v1/game/benchmark-exam{path}", json=json)
    finally:
        app.dependency_overrides.clear()


async def test_starting_a_sitting_is_a_201() -> None:
    service = StubService()

    response = await _request(service, "POST", "/attempts", {"cap_level": 10})

    assert response.status_code == 201
    assert response.json()["attempt_id"] == "a1"
    assert service.calls == [("start", "user-1", 10)]


async def test_an_answer_response_carries_no_verdict() -> None:
    service = StubService()

    response = await _request(
        service, "POST", "/attempts/a1/answers", {"challenge_id": "c1", "selected_option_id": "o1"}
    )

    assert response.status_code == 200
    assert set(response.json()) == {"attempt_id", "answered_count", "total"}
    assert service.calls == [("answer", "user-1", "a1", "c1", "o1", None)]


async def test_history_is_capped() -> None:
    response = await _request(StubService(), "GET", "/attempts?limit=51")

    assert response.status_code == 422


async def test_domain_errors_map_to_statuses() -> None:
    cases: list[tuple[Exception, str, str, int]] = [
        (BenchmarkExamNotEligibleError(), "POST", "/attempts", 400),
        (BenchmarkExamAlreadyClearedError(), "POST", "/attempts", 409),
        (BenchmarkExamNotEnoughQuestionsError(), "POST", "/attempts", 409),
        (BenchmarkExamAttemptNotFoundError(), "POST", "/attempts/a1/submit", 404),
        (BenchmarkExamAttemptClosedError(), "POST", "/attempts/a1/answers", 409),
        (BenchmarkExamQuestionAlreadyAnsweredError(), "POST", "/attempts/a1/answers", 409),
        (BenchmarkExamQuestionNotInAttemptError(), "POST", "/attempts/a1/answers", 400),
        (InvalidAnswerSubmissionError(), "POST", "/attempts/a1/answers", 400),
        (ChallengeOptionNotFoundError(), "POST", "/attempts/a1/answers", 400),
    ]
    body = {"cap_level": 10, "challenge_id": "c1", "selected_option_id": "o1"}
    for error, method, path, expected in cases:
        response = await _request(StubService(error), method, path, body)
        assert response.status_code == expected, (type(error).__name__, response.status_code)
