from typing import NoReturn

from fastapi import HTTPException, status

from app.core.exceptions import (
    BenchmarkExamNotEligibleError,
    GameClassNotFoundError,
    InvalidEquipmentError,
    InvalidLoadoutError,
    ItemAlreadyOwnedError,
    ItemNotForSaleError,
    ItemNotFoundError,
    NotEnoughGoldError,
    SkillAlreadyOwnedError,
    SkillLockedError,
    SkillNotFoundError,
)

_NOT_FOUND_ERRORS = (
    GameClassNotFoundError,
    SkillNotFoundError,
    ItemNotFoundError,
)
# Conditions the caller could fix by choosing differently, all 400.
_BAD_REQUEST_ERRORS = (
    SkillLockedError,
    NotEnoughGoldError,
    InvalidLoadoutError,
    InvalidEquipmentError,
    BenchmarkExamNotEligibleError,
    ItemNotForSaleError,
)
# Already have it, so the request is not wrong -- it is simply too late.
_CONFLICT_ERRORS = (
    SkillAlreadyOwnedError,
    ItemAlreadyOwnedError,
)


def raise_game_http_error(exc: Exception) -> NoReturn:
    """Translate game domain errors into the matching HTTPException."""
    if isinstance(exc, _NOT_FOUND_ERRORS):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "Resource not found",
        ) from exc
    if isinstance(exc, _CONFLICT_ERRORS):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc) or "You already own that",
        ) from exc
    if isinstance(exc, _BAD_REQUEST_ERRORS):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc) or "That is not allowed",
        ) from exc
    raise exc
