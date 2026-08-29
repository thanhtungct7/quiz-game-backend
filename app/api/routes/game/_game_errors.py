from typing import NoReturn

from fastapi import HTTPException, status

from app.core.exceptions import (
    GameClassNotFoundError,
    InvalidEquipmentError,
    InvalidLoadoutError,
    NotEnoughGoldError,
    SkillAlreadyOwnedError,
    SkillLockedError,
    SkillNotFoundError,
)

_NOT_FOUND_ERRORS = (GameClassNotFoundError, SkillNotFoundError)
# Conditions the caller could fix by choosing differently, all 400.
_BAD_REQUEST_ERRORS = (
    SkillLockedError,
    NotEnoughGoldError,
    InvalidLoadoutError,
    InvalidEquipmentError,
)


def raise_game_http_error(exc: Exception) -> NoReturn:
    """Translate game domain errors into the matching HTTPException."""
    if isinstance(exc, _NOT_FOUND_ERRORS):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "Resource not found",
        ) from exc
    if isinstance(exc, SkillAlreadyOwnedError):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc) or "You already own this skill",
        ) from exc
    if isinstance(exc, _BAD_REQUEST_ERRORS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "That is not allowed",
        ) from exc
    raise exc
