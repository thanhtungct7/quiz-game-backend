from datetime import UTC, datetime

import httpx

from app.api.dependencies import get_conversation_service, get_current_user
from app.core.exceptions import (
    AiUnavailableError,
    ConversationAwaitingReplyError,
    ConversationClosedError,
    ConversationNotFoundError,
    ConversationTooShortError,
    DailyConversationLimitError,
    ScenarioNotFoundError,
)
from app.main import app
from app.models.auth.user import User
from app.models.conversation.conversation_message import MessageRole
from app.schemas.conversation.conversation import MessageRead, TurnRead

NOW = datetime(2026, 9, 17, 3, 0, tzinfo=UTC)


class StubService:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[object, ...]] = []

    def _record(self, *call: object) -> None:
        self.calls.append(call)
        if self.error is not None:
            raise self.error

    def list_scenarios(self) -> list[object]:
        return []

    async def start(self, user_id: str, scenario_code: str, now: datetime) -> None:
        self._record("start", user_id, scenario_code)

    async def send(self, user_id: str, session_id: str, content: str) -> TurnRead:
        self._record("send", user_id, session_id, content)
        message = MessageRead(
            id="m",
            seq=2,
            role=MessageRole.USER,
            content=content,
            translation_vi=None,
            created_at=NOW,
        )
        return TurnRead(
            user_message=message,
            assistant_message=message,
            user_turns=1,
            max_turns=12,
            should_finish=False,
        )

    async def retry(self, user_id: str, session_id: str) -> None:
        self._record("retry", user_id, session_id)

    async def hint(self, user_id: str, session_id: str) -> None:
        self._record("hint", user_id, session_id)

    async def finish(self, user_id: str, session_id: str, now: datetime) -> None:
        self._record("finish", user_id, session_id)

    async def delete(self, user_id: str, session_id: str) -> None:
        self._record("delete", user_id, session_id)


async def _request(
    service: StubService, method: str, path: str, json: dict[str, object] | None = None
) -> httpx.Response:
    app.dependency_overrides[get_current_user] = lambda: User(id="user-1", email="u@example.com")
    app.dependency_overrides[get_conversation_service] = lambda: service
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, f"/api/v1/conversations{path}", json=json)
    finally:
        app.dependency_overrides.clear()


async def test_a_message_is_trimmed_and_sent_for_the_caller() -> None:
    service = StubService()

    response = await _request(service, "POST", "/s1/messages", {"content": "  Hello!  "})

    assert response.status_code == 200
    assert service.calls == [("send", "user-1", "s1", "Hello!")]


async def test_blank_and_overlong_messages_are_rejected() -> None:
    for content in ["   ", "x" * 501]:
        response = await _request(StubService(), "POST", "/s1/messages", {"content": content})
        assert response.status_code == 422


async def test_deleting_is_a_204() -> None:
    service = StubService()

    response = await _request(service, "DELETE", "/s1")

    assert response.status_code == 204
    assert service.calls == [("delete", "user-1", "s1")]


async def test_domain_errors_map_to_statuses() -> None:
    cases: list[tuple[Exception, str, str, int]] = [
        (ScenarioNotFoundError(), "POST", "", 404),
        (DailyConversationLimitError(), "POST", "", 429),
        (AiUnavailableError(), "POST", "", 503),
        (ConversationNotFoundError(), "POST", "/s1/messages", 404),
        (ConversationClosedError(), "POST", "/s1/messages", 409),
        (ConversationAwaitingReplyError(), "POST", "/s1/messages", 409),
        (AiUnavailableError(), "POST", "/s1/retry", 503),
        (ConversationClosedError(), "POST", "/s1/hint", 409),
        (ConversationTooShortError(), "POST", "/s1/finish", 400),
        (ConversationNotFoundError(), "DELETE", "/s1", 404),
    ]
    body = {"scenario_code": "cafe_order", "content": "Hi"}
    for error, method, path, expected in cases:
        response = await _request(StubService(error), method, path, body)
        assert response.status_code == expected, (type(error).__name__, response.status_code)
