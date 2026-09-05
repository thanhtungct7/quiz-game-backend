"""The single WebSocket endpoint that carries a whole lesson battle.

Every inbound frame is `{"type": ..., "data": {...}}` and is validated against
a Pydantic model before the engine sees it, so a malformed or hostile client
gets an `error` frame instead of reaching the battle state.
"""

import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, ValidationError

from app.api.dependencies import CurrentWebSocketUser
from app.models.auth.user import User
from app.schemas.pve.events import (
    AnswerSubmitPayload,
    BattleStartPayload,
    ClientEnvelope,
    ClientEvent,
    ErrorCode,
    ErrorData,
    PingPayload,
    ServerEvent,
    SkillUsePayload,
    envelope,
)
from app.services.pve.battle_runtime import engine

logger = logging.getLogger(__name__)

router = APIRouter()


@router.websocket("/ws")
async def battle_websocket(websocket: WebSocket, user: CurrentWebSocketUser) -> None:
    """Accept the socket, then dispatch every frame until disconnect.

    The `finally` always runs `on_disconnect`, which ends the fight: a battle
    is not resumable, by design.
    """
    await websocket.accept()
    await engine.on_connect(user, websocket)
    try:
        while True:
            raw = await websocket.receive_text()
            await _dispatch(user, websocket, raw)
    except WebSocketDisconnect:
        pass
    finally:
        await engine.on_disconnect(user.id, websocket)


async def _dispatch(user: User, websocket: WebSocket, raw: str) -> None:
    """Parse one inbound frame and route it to the matching engine call,
    emitting an `error` frame for anything malformed instead of raising."""
    try:
        message = ClientEnvelope.model_validate_json(raw)
    except ValidationError:
        await _error(websocket, ErrorCode.INVALID_PAYLOAD, "Malformed message")
        return

    try:
        event = ClientEvent(message.type)
    except ValueError:
        await _error(websocket, ErrorCode.UNKNOWN_EVENT, f"Unknown event {message.type!r}")
        return

    match event:
        case ClientEvent.BATTLE_START:
            start = _parse(BattleStartPayload, message.data)
            if start is None:
                await _invalid(websocket)
                return
            await engine.start_battle(user, websocket, start.lesson_id)

        case ClientEvent.ANSWER_SUBMIT:
            answer = _parse(AnswerSubmitPayload, message.data)
            if answer is None:
                await _invalid(websocket)
                return
            await engine.submit_answer(
                user.id, websocket, answer.token, answer.option_id
            )

        case ClientEvent.SKILL_USE:
            skill = _parse(SkillUsePayload, message.data)
            if skill is None:
                await _invalid(websocket)
                return
            await engine.use_skill(user.id, websocket, skill.skill_code)

        case ClientEvent.BATTLE_LEAVE:
            await engine.leave_battle(user.id)

        case ClientEvent.PING:
            # Tolerant of a bare ping: an old client that sends no stamp still
            # gets the server's clock, which is the half of the exchange that
            # matters for drawing a cast bar.
            ping = _parse(PingPayload, message.data) or PingPayload()
            await engine.pong(websocket, ping.client_time_ms)


def _parse[T: BaseModel](model: type[T], data: dict[str, object]) -> T | None:
    try:
        return model.model_validate(data)
    except ValidationError:
        return None


async def _invalid(websocket: WebSocket) -> None:
    await _error(websocket, ErrorCode.INVALID_PAYLOAD, "Invalid payload for this event")


async def _error(websocket: WebSocket, code: ErrorCode, message: str) -> None:
    await websocket.send_json(
        envelope(ServerEvent.ERROR, ErrorData(code=code, message=message))
    )
