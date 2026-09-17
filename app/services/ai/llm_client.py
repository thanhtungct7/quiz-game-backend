"""Calling a large language model.

The only module that talks to the AI provider -- DeepSeek, through its
OpenAI-compatible Chat Completions endpoint over plain httpx. Everything above
it depends on `LlmClient`, so tests -- and a development machine with no API
key -- never reach the network.

History is kept by the caller and sent in full on every call rather than held
in any provider-side session, so switching models or providers loses nothing.
"""

import asyncio
import json
import logging
import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol, TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from app.core.config import Settings, settings
from app.core.exceptions import AiUnavailableError

logger = logging.getLogger(__name__)

SchemaT = TypeVar("SchemaT", bound=BaseModel)

# Rate limited, or the provider having a bad moment: worth one more try, not more --
# a learner is waiting on the other end.
RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})
RETRY_DELAY_SECONDS = 1.0
# Some chat APIs reject a conversation that does not open with the user speaking.
CONVERSATION_START = "(The conversation starts.)"


@dataclass(frozen=True)
class ChatTurn:
    role: Literal["user", "model"]
    text: str


class LlmClient(Protocol):
    async def generate_text(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[ChatTurn],
        max_output_tokens: int,
    ) -> str:
        """The model's next turn. Raises AiUnavailableError, never returns empty."""
        ...

    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[SchemaT],
        max_output_tokens: int,
    ) -> SchemaT:
        """One answer shaped as `schema`. Raises AiUnavailableError on any failure,
        including an answer that does not validate."""
        ...


def normalize_turns(turns: Sequence[ChatTurn]) -> list[ChatTurn]:
    """Open with the user and alternate roles, merging neighbours that do not.

    A history trimmed to its last N messages can start on the model's line, and a
    failed reply leaves two user lines in a row.
    """
    merged: list[ChatTurn] = []
    for turn in turns:
        if merged and merged[-1].role == turn.role:
            merged[-1] = ChatTurn(turn.role, f"{merged[-1].text}\n{turn.text}")
        else:
            merged.append(turn)
    if not merged or merged[0].role != "user":
        merged.insert(0, ChatTurn("user", CONVERSATION_START))
    return merged


def json_instructions(schema: type[BaseModel]) -> str:
    """DeepSeek's JSON mode only promises valid JSON, not a shape: the word "json"
    must be in the prompt and the shape spelled out, so the schema goes with it."""
    return (
        "Respond with a single json object and nothing else. It must match this JSON"
        f" Schema:\n{json.dumps(schema.model_json_schema(), ensure_ascii=False)}"
    )


class DeepSeekClient:
    def __init__(
        self,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._http = httpx.AsyncClient(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout_seconds,
            transport=transport,
        )

    async def generate_text(
        self,
        *,
        model: str,
        system: str,
        turns: Sequence[ChatTurn],
        max_output_tokens: int,
    ) -> str:
        messages = [{"role": "system", "content": system}] + [
            {"role": "user" if turn.role == "user" else "assistant", "content": turn.text}
            for turn in normalize_turns(turns)
        ]
        text = (await self._complete(model, messages, max_output_tokens)).strip()
        if not text:
            raise AiUnavailableError("The AI returned an empty reply")
        return text

    async def generate_json(
        self,
        *,
        model: str,
        system: str,
        prompt: str,
        schema: type[SchemaT],
        max_output_tokens: int,
    ) -> SchemaT:
        messages = [
            {"role": "system", "content": f"{system}\n\n{json_instructions(schema)}"},
            {"role": "user", "content": prompt},
        ]
        # JSON mode is documented to come back empty now and then; that one is
        # worth asking again, a wrong shape is not.
        content = await self._complete(model, messages, max_output_tokens, json_mode=True)
        if not content.strip():
            content = await self._complete(model, messages, max_output_tokens, json_mode=True)
        try:
            return schema.model_validate_json(content)
        except ValidationError as exc:
            logger.warning("DeepSeek %s returned JSON not matching %s", model, schema.__name__)
            raise AiUnavailableError("The AI returned an unreadable answer") from exc

    async def _complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        max_output_tokens: int,
        *,
        json_mode: bool = False,
    ) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_output_tokens,
            "stream": False,
            # On by default. A practice reply needs no chain of thought, and waiting
            # for one would make every turn slow and several times the price.
            "thinking": {"type": "disabled"},
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        started = time.monotonic()
        response = await self._post(model, body)
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        usage = data.get("usage") or {}
        # Model, timing and token counts only: what learners write is never logged.
        logger.info(
            "DeepSeek %s: %.2fs, prompt %s tokens (cached %s), output %s tokens, finish %s",
            model,
            time.monotonic() - started,
            usage.get("prompt_tokens"),
            usage.get("prompt_cache_hit_tokens"),
            usage.get("completion_tokens"),
            choice.get("finish_reason"),
        )
        return str((choice.get("message") or {}).get("content") or "")

    async def _post(self, model: str, body: dict[str, Any]) -> httpx.Response:
        for attempt in (1, 2):
            try:
                response = await self._http.post("/chat/completions", json=body)
            except httpx.HTTPError as exc:
                logger.warning("DeepSeek %s call failed (attempt %d): %r", model, attempt, exc)
                if attempt == 2:
                    raise AiUnavailableError("The AI service is unavailable") from exc
            else:
                if response.is_success:
                    return response
                # 401: bad key. 402: the account is out of balance. Neither heals on retry.
                logger.warning(
                    "DeepSeek %s answered %d (attempt %d): %s",
                    model,
                    response.status_code,
                    attempt,
                    response.text[:300],
                )
                if response.status_code not in RETRYABLE_STATUS_CODES or attempt == 2:
                    raise AiUnavailableError("The AI service is unavailable")
            await asyncio.sleep(RETRY_DELAY_SECONDS)
        raise AssertionError("unreachable")


def build_llm_client(config: Settings) -> LlmClient | None:
    if config.deepseek_api_key is None:
        return None
    return DeepSeekClient(
        config.deepseek_api_key.get_secret_value(),
        config.deepseek_base_url,
        config.deepseek_timeout_seconds,
    )


_client: LlmClient | None = None
_built = False


def get_llm_client() -> LlmClient | None:
    """One per process, so the connection pool is shared. None when no API key
    is configured."""
    global _client, _built
    if not _built:
        _client = build_llm_client(settings)
        _built = True
    return _client
