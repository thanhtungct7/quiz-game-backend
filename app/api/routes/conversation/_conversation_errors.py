from typing import NoReturn

from fastapi import HTTPException, status

from app.core.exceptions import (
    AiUnavailableError,
    ConversationAwaitingReplyError,
    ConversationClosedError,
    ConversationNotFoundError,
    ConversationTooShortError,
    DailyConversationLimitError,
    ScenarioNotFoundError,
)

_STATUS_BY_ERROR: tuple[tuple[type[Exception], int], ...] = (
    (ScenarioNotFoundError, status.HTTP_404_NOT_FOUND),
    (ConversationNotFoundError, status.HTTP_404_NOT_FOUND),
    (ConversationClosedError, status.HTTP_409_CONFLICT),
    (ConversationAwaitingReplyError, status.HTTP_409_CONFLICT),
    (ConversationTooShortError, status.HTTP_400_BAD_REQUEST),
    (DailyConversationLimitError, status.HTTP_429_TOO_MANY_REQUESTS),
    # Not the caller's fault and worth trying again later.
    (AiUnavailableError, status.HTTP_503_SERVICE_UNAVAILABLE),
)


def raise_conversation_http_error(exc: Exception) -> NoReturn:
    """Translate conversation domain errors into the matching HTTPException."""
    for error_type, status_code in _STATUS_BY_ERROR:
        if isinstance(exc, error_type):
            raise HTTPException(status_code=status_code, detail=str(exc)) from exc
    raise exc
