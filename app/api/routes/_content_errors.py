from typing import NoReturn

from fastapi import HTTPException, status

from app.core.exceptions import (
    ChallengeNotFoundError,
    ChallengeOptionNotFoundError,
    CourseNotFoundError,
    DuplicateOrderIndexError,
    InvalidChallengeOptionsError,
    LessonNotFoundError,
    UnitNotFoundError,
)

_NOT_FOUND_ERRORS = (
    CourseNotFoundError,
    UnitNotFoundError,
    LessonNotFoundError,
    ChallengeNotFoundError,
    ChallengeOptionNotFoundError,
)
_BAD_REQUEST_ERRORS = (InvalidChallengeOptionsError, DuplicateOrderIndexError)


def raise_content_http_error(exc: Exception) -> NoReturn:
    """Translate course-content domain errors into the matching HTTPException."""
    if isinstance(exc, _NOT_FOUND_ERRORS):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "Resource not found",
        ) from exc
    if isinstance(exc, _BAD_REQUEST_ERRORS):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    raise exc
