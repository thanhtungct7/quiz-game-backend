import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.core.exceptions import AiUnavailableError
from app.services.ai import llm_client
from app.services.ai.llm_client import ChatTurn, DeepSeekClient


class Answer(BaseModel):
    score: int
    words: list[str]


def _completion(content: str | None) -> dict[str, Any]:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
    }


def _client(
    handler: Callable[[httpx.Request], httpx.Response],
) -> tuple[DeepSeekClient, list[dict[str, Any]]]:
    bodies: list[dict[str, Any]] = []

    def record(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/chat/completions"
        assert request.headers["Authorization"] == "Bearer test-key"
        bodies.append(json.loads(request.content))
        return handler(request)

    client = DeepSeekClient(
        "test-key", "https://api.deepseek.test", 5, transport=httpx.MockTransport(record)
    )
    return client, bodies


@pytest.fixture(autouse=True)
def _no_retry_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_client, "RETRY_DELAY_SECONDS", 0)


async def test_text_request_has_system_prompt_history_and_thinking_off() -> None:
    client, bodies = _client(lambda _: httpx.Response(200, json=_completion("  Hi!  ")))

    reply = await client.generate_text(
        model="deepseek-flash",
        system="Be a barista.",
        turns=[ChatTurn("model", "Hello!"), ChatTurn("user", "A tea.")],
        max_output_tokens=100,
    )

    assert reply == "Hi!"
    body = bodies[0]
    assert body["model"] == "deepseek-flash"
    assert body["max_tokens"] == 100
    assert body["thinking"] == {"type": "disabled"}
    assert "response_format" not in body
    assert [m["role"] for m in body["messages"]] == ["system", "user", "assistant", "user"]
    assert body["messages"][0]["content"] == "Be a barista."


async def test_json_request_asks_for_json_and_validates_the_shape() -> None:
    client, bodies = _client(
        lambda _: httpx.Response(200, json=_completion('{"score": 7, "words": ["tea"]}'))
    )

    answer = await client.generate_json(
        model="deepseek-flash",
        system="Grade.",
        prompt="Transcript",
        schema=Answer,
        max_output_tokens=100,
    )

    assert answer == Answer(score=7, words=["tea"])
    body = bodies[0]
    assert body["response_format"] == {"type": "json_object"}
    # JSON mode requires the word "json" in the prompt.
    assert "json" in body["messages"][0]["content"]
    assert '"score"' in body["messages"][0]["content"]


async def test_an_empty_json_answer_is_asked_for_once_more() -> None:
    answers = iter([_completion(""), _completion('{"score": 1, "words": []}')])
    client, bodies = _client(lambda _: httpx.Response(200, json=next(answers)))

    answer = await client.generate_json(
        model="m", system="s", prompt="p", schema=Answer, max_output_tokens=10
    )

    assert answer.score == 1
    assert len(bodies) == 2


async def test_a_wrong_json_shape_is_unavailable() -> None:
    client, _ = _client(lambda _: httpx.Response(200, json=_completion('{"score": "high"}')))

    with pytest.raises(AiUnavailableError):
        await client.generate_json(
            model="m", system="s", prompt="p", schema=Answer, max_output_tokens=10
        )


async def test_a_server_error_is_retried_once() -> None:
    statuses = iter([503, 200])

    def handler(_: httpx.Request) -> httpx.Response:
        code = next(statuses)
        return httpx.Response(code, json=_completion("ok") if code == 200 else {})

    client, bodies = _client(handler)

    assert (
        await client.generate_text(
            model="m", system="s", turns=[ChatTurn("user", "hi")], max_output_tokens=10
        )
        == "ok"
    )
    assert len(bodies) == 2


async def test_errors_that_do_not_heal_are_not_retried() -> None:
    for code in (401, 402, 400):
        client, bodies = _client(lambda _, code=code: httpx.Response(code, json={}))

        with pytest.raises(AiUnavailableError):
            await client.generate_text(
                model="m", system="s", turns=[ChatTurn("user", "hi")], max_output_tokens=10
            )
        assert len(bodies) == 1, code


async def test_persistent_failure_and_empty_replies_are_unavailable() -> None:
    client, bodies = _client(lambda _: httpx.Response(500, json={}))
    with pytest.raises(AiUnavailableError):
        await client.generate_text(
            model="m", system="s", turns=[ChatTurn("user", "hi")], max_output_tokens=10
        )
    assert len(bodies) == 2

    client, _ = _client(lambda _: httpx.Response(200, json=_completion(None)))
    with pytest.raises(AiUnavailableError):
        await client.generate_text(
            model="m", system="s", turns=[ChatTurn("user", "hi")], max_output_tokens=10
        )


async def test_network_errors_are_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("slow", request=request)

    client, bodies = _client(handler)

    with pytest.raises(AiUnavailableError):
        await client.generate_text(
            model="m", system="s", turns=[ChatTurn("user", "hi")], max_output_tokens=10
        )
    assert len(bodies) == 2
