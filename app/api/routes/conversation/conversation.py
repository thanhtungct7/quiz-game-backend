from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Query, Response, status

from app.api.dependencies import ConversationServiceDependency, CurrentUser
from app.api.rate_limit import limit_by_user
from app.api.routes.conversation._conversation_errors import raise_conversation_http_error
from app.core import rate_limit_policies as limits
from app.core.exceptions import ApplicationError
from app.schemas.conversation.conversation import (
    ConversationDetailRead,
    ConversationStartRequest,
    ConversationSummaryRead,
    HintRead,
    MessageSendRequest,
    ScenarioRead,
    TranslationRead,
    TurnRead,
)

router = APIRouter()


@router.get("/scenarios", response_model=list[ScenarioRead])
async def list_scenarios(
    _: CurrentUser, service: ConversationServiceDependency
) -> list[ScenarioRead]:
    return service.list_scenarios()


@router.post(
    "",
    response_model=ConversationDetailRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[limit_by_user("conversation-start", limits.CONVERSATION_START_PER_USER)],
)
async def start_conversation(
    payload: ConversationStartRequest,
    current_user: CurrentUser,
    service: ConversationServiceDependency,
) -> ConversationDetailRead:
    """Open a conversation on one scenario. The AI's opening line comes back with it."""
    try:
        return await service.start(current_user.id, payload.scenario_code, datetime.now(UTC))
    except ApplicationError as exc:
        raise_conversation_http_error(exc)


@router.get("", response_model=list[ConversationSummaryRead])
async def list_conversations(
    current_user: CurrentUser,
    service: ConversationServiceDependency,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    before: datetime | None = None,
) -> list[ConversationSummaryRead]:
    """Newest first. Pass the last row's `started_at` as `before` for the next page."""
    return await service.list_conversations(current_user.id, limit=limit, before=before)


@router.get("/{session_id}", response_model=ConversationDetailRead)
async def get_conversation(
    session_id: str, current_user: CurrentUser, service: ConversationServiceDependency
) -> ConversationDetailRead:
    try:
        return await service.get(current_user.id, session_id)
    except ApplicationError as exc:
        raise_conversation_http_error(exc)


@router.post(
    "/{session_id}/messages",
    response_model=TurnRead,
    dependencies=[limit_by_user("conversation-message", limits.CONVERSATION_MESSAGE_PER_USER)],
)
async def send_message(
    session_id: str,
    payload: MessageSendRequest,
    current_user: CurrentUser,
    service: ConversationServiceDependency,
) -> TurnRead:
    """Say one line and get the AI's reply. On 503 the line is kept: call `/retry`."""
    try:
        return await service.send(current_user.id, session_id, payload.content)
    except ApplicationError as exc:
        raise_conversation_http_error(exc)


@router.post(
    "/{session_id}/retry",
    response_model=TurnRead,
    dependencies=[limit_by_user("conversation-message", limits.CONVERSATION_MESSAGE_PER_USER)],
)
async def retry_reply(
    session_id: str, current_user: CurrentUser, service: ConversationServiceDependency
) -> TurnRead:
    """Ask again for a reply to the last line, after the AI failed on it."""
    try:
        return await service.retry(current_user.id, session_id)
    except ApplicationError as exc:
        raise_conversation_http_error(exc)


@router.post(
    "/{session_id}/hint",
    response_model=HintRead,
    dependencies=[limit_by_user("conversation-extra", limits.CONVERSATION_EXTRA_PER_USER)],
)
async def get_hint(
    session_id: str, current_user: CurrentUser, service: ConversationServiceDependency
) -> HintRead:
    """A few things the learner could say next. Not stored, not a turn."""
    try:
        return await service.hint(current_user.id, session_id)
    except ApplicationError as exc:
        raise_conversation_http_error(exc)


@router.post(
    "/{session_id}/messages/{message_id}/translate",
    response_model=TranslationRead,
    dependencies=[limit_by_user("conversation-extra", limits.CONVERSATION_EXTRA_PER_USER)],
)
async def translate_message(
    session_id: str,
    message_id: str,
    current_user: CurrentUser,
    service: ConversationServiceDependency,
) -> TranslationRead:
    """The line in Vietnamese. Stored, so asking again does not call the AI."""
    try:
        return await service.translate(current_user.id, session_id, message_id)
    except ApplicationError as exc:
        raise_conversation_http_error(exc)


@router.post("/{session_id}/finish", response_model=ConversationDetailRead)
async def finish_conversation(
    session_id: str, current_user: CurrentUser, service: ConversationServiceDependency
) -> ConversationDetailRead:
    """End the conversation and get feedback on it. Safe to repeat."""
    try:
        return await service.finish(current_user.id, session_id, datetime.now(UTC))
    except ApplicationError as exc:
        raise_conversation_http_error(exc)


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(
    session_id: str, current_user: CurrentUser, service: ConversationServiceDependency
) -> Response:
    try:
        await service.delete(current_user.id, session_id)
    except ApplicationError as exc:
        raise_conversation_http_error(exc)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
