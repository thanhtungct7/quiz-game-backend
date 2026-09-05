from typing import NoReturn

from fastapi import HTTPException, status

from app.core.exceptions import (
    CourseNotFoundError,
    LessonNotFoundError,
    MonsterUnavailableError,
)

_NOT_FOUND_ERRORS = (LessonNotFoundError, CourseNotFoundError)


def raise_pve_http_error(exc: Exception) -> NoReturn:
    """Translate PvE domain errors into the matching HTTPException."""
    if isinstance(exc, _NOT_FOUND_ERRORS):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "Resource not found",
        ) from exc
    if isinstance(exc, MonsterUnavailableError):
        # The catalog has not been seeded. Nothing the caller can fix, and not
        # a 404 either: the lesson is there, its monster is not.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="No monster is available for this lesson",
        ) from exc
    raise exc
