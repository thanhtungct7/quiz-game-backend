from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Response, UploadFile, status

from app.api.dependencies import CurrentUser, UserServiceDependency
from app.core.config import settings
from app.core.exceptions import (
    AvatarNotFoundError,
    AvatarStorageUnavailableError,
    AvatarTooLargeError,
    InvalidAvatarError,
)
from app.schemas.auth.user import UserRead, UserUpdate
from app.services.auth.avatar_storage import validate_avatar
from app.services.auth.user_service import build_user_read

router = APIRouter()

_UPLOAD_CHUNK_BYTES = 64 * 1024
# The URL carries a `?v=` cache buster tied to the file id, so a cached copy can never be
# stale: a different avatar is always a different URL.
_AVATAR_CACHE_CONTROL = "public, max-age=31536000, immutable"


@router.get("/me", response_model=UserRead)
async def read_current_user(current_user: CurrentUser) -> UserRead:
    return build_user_read(current_user, settings)


@router.patch("/me", response_model=UserRead)
async def update_current_user(
    payload: UserUpdate,
    current_user: CurrentUser,
    user_service: UserServiceDependency,
) -> UserRead:
    updated = await user_service.update_profile(current_user, payload)
    return build_user_read(updated, settings)


@router.post("/me/avatar", response_model=UserRead)
async def upload_current_user_avatar(
    current_user: CurrentUser,
    user_service: UserServiceDependency,
    file: Annotated[UploadFile, File()],
) -> UserRead:
    content = await _read_capped(file, settings.max_avatar_bytes)
    try:
        content_type = validate_avatar(content, file.content_type, settings.max_avatar_bytes)
        updated = await user_service.replace_avatar(current_user, content, content_type)
    except AvatarTooLargeError as exc:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE, detail=str(exc)
        ) from exc
    except InvalidAvatarError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except AvatarStorageUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return build_user_read(updated, settings)


@router.delete("/me/avatar", response_model=UserRead)
async def delete_current_user_avatar(
    current_user: CurrentUser,
    user_service: UserServiceDependency,
) -> UserRead:
    try:
        updated = await user_service.remove_avatar(current_user)
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AvatarStorageUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
    return build_user_read(updated, settings)


@router.get("/{user_id}/avatar", response_class=Response)
async def read_user_avatar(user_id: str, user_service: UserServiceDependency) -> Response:
    """Serve an uploaded avatar. Deliberately unauthenticated: the image loader on the
    client fetches it outside the API stack and carries no bearer token, an avatar is not
    sensitive, and the user id it is keyed by is an unguessable UUID."""
    try:
        content, content_type = await user_service.read_avatar(user_id)
    except AvatarNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except AvatarStorageUnavailableError as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc

    return Response(
        content=content,
        media_type=content_type,
        headers={"Cache-Control": _AVATAR_CACHE_CONTROL},
    )


async def _read_capped(file: UploadFile, limit: int) -> bytes:
    """Read the upload but stop as soon as it is over the limit.

    `file.read()` with no argument would buffer the whole body first, and the client's
    Content-Length is not evidence of anything.
    """
    chunks: list[bytes] = []
    total = 0
    while chunk := await file.read(_UPLOAD_CHUNK_BYTES):
        total += len(chunk)
        if total > limit:
            raise HTTPException(
                status_code=status.HTTP_413_CONTENT_TOO_LARGE,
                detail=f"Avatar must be at most {limit // 1024} KB",
            )
        chunks.append(chunk)
    return b"".join(chunks)
