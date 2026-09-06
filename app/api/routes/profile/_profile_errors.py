from typing import NoReturn

from fastapi import HTTPException, status

from app.core.exceptions import UserNotFoundError


def raise_profile_http_error(exc: Exception) -> NoReturn:
    """Translate profile domain errors into the matching HTTPException."""
    if isinstance(exc, UserNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No such player",
        ) from exc
    raise exc
