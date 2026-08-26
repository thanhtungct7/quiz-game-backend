from typing import NoReturn

from fastapi import HTTPException, status

from app.core.exceptions import (
    DuoMatchNotFoundError,
    DuoRoomNotFoundError,
    NotMatchMemberError,
)

_NOT_FOUND_ERRORS = (DuoMatchNotFoundError, DuoRoomNotFoundError)


def raise_duo_http_error(exc: Exception) -> NoReturn:
    """Translate duo domain errors into the matching HTTPException."""
    if isinstance(exc, _NOT_FOUND_ERRORS):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "Resource not found",
        ) from exc
    if isinstance(exc, NotMatchMemberError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You did not play in this match",
        ) from exc
    raise exc
