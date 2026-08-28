from datetime import UTC, datetime
from typing import NoReturn

import pytest
from fastapi import HTTPException, status

from app.api.routes.auth.users import (
    delete_current_user_avatar,
    read_current_user,
    read_user_avatar,
    update_current_user,
    upload_current_user_avatar,
)
from app.core.config import settings
from app.core.exceptions import (
    AvatarNotFoundError,
    AvatarStorageUnavailableError,
    AvatarTooLargeError,
    InvalidAvatarError,
)
from app.models.auth.user import User
from app.schemas.auth.user import UserUpdate
from app.services.auth.avatar_storage import InMemoryAvatarStorage, validate_avatar
from app.services.auth.user_service import UserService, build_user_read

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
WEBP_BYTES = b"RIFF" + b"\x00\x00\x00\x00" + b"WEBP" + b"\x00" * 32


def _make_user(**overrides: object) -> User:
    user = User(
        id="user-1",
        email="player@example.com",
        username="Player",
        is_active=True,
    )
    user.created_at = datetime.now(UTC)
    for name, value in overrides.items():
        setattr(user, name, value)
    return user


class FakeUserRepository:
    def __init__(self, user: User) -> None:
        self.user = user
        self.updates: list[dict[str, object]] = []

    async def update(self, user: User, **fields: object) -> User:
        self.updates.append(dict(fields))
        for name, value in fields.items():
            setattr(user, name, value)
        return user

    async def get_by_id(self, user_id: str) -> User | None:
        return self.user if self.user.id == user_id else None


def _make_service(user: User) -> tuple[UserService, FakeUserRepository, InMemoryAvatarStorage]:
    repository = FakeUserRepository(user)
    storage = InMemoryAvatarStorage()
    service = UserService(
        user_repository=repository,  # type: ignore[arg-type]
        avatar_storage=storage,
        config=settings,
    )
    return service, repository, storage


class FailingUserService:
    def __init__(self, error: Exception) -> None:
        self.error = error

    async def update_profile(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def replace_avatar(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def remove_avatar(self, *_: object, **__: object) -> NoReturn:
        raise self.error

    async def read_avatar(self, *_: object, **__: object) -> NoReturn:
        raise self.error


class StubUpload:
    """Stands in for Starlette's UploadFile: only `read(size)` and `content_type` are used."""

    def __init__(self, content: bytes, content_type: str | None = "image/png") -> None:
        self.content = content
        self.content_type = content_type
        self._offset = 0

    async def read(self, size: int = -1) -> bytes:
        if size < 0:
            size = len(self.content) - self._offset
        chunk = self.content[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


# --- validate_avatar -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("content", "expected"),
    [(PNG_BYTES, "image/png"), (JPEG_BYTES, "image/jpeg"), (WEBP_BYTES, "image/webp")],
)
def test_validate_avatar_accepts_the_three_supported_formats(content: bytes, expected: str) -> None:
    assert validate_avatar(content, expected, max_bytes=1024) == expected


def test_validate_avatar_trusts_the_bytes_over_the_declared_type() -> None:
    """A PNG announced as image/jpeg is stored as a PNG -- otherwise the proxy would
    serve it back under a media type it is not."""
    assert validate_avatar(PNG_BYTES, "image/jpeg", max_bytes=1024) == "image/png"


def test_validate_avatar_rejects_a_non_image_wearing_an_image_content_type() -> None:
    with pytest.raises(InvalidAvatarError):
        validate_avatar(b"#!/bin/sh\nrm -rf /", "image/png", max_bytes=1024)


def test_validate_avatar_rejects_a_disallowed_declared_type() -> None:
    with pytest.raises(InvalidAvatarError):
        validate_avatar(PNG_BYTES, "text/plain", max_bytes=1024)


def test_validate_avatar_rejects_an_empty_file() -> None:
    with pytest.raises(InvalidAvatarError):
        validate_avatar(b"", "image/png", max_bytes=1024)


def test_validate_avatar_rejects_content_over_the_limit() -> None:
    with pytest.raises(AvatarTooLargeError):
        validate_avatar(PNG_BYTES, "image/png", max_bytes=8)


# --- build_user_read -------------------------------------------------------------------


def test_uploaded_avatar_is_served_through_this_api_with_a_cache_buster() -> None:
    user = _make_user(avatar_file_id="drive-file-abcdef", avatar_url="https://google/pic.jpg")

    result = build_user_read(user, settings)

    assert result.avatar_url == f"{settings.api_v1_prefix}/users/user-1/avatar?v=drive-fi"
    assert result.has_uploaded_avatar


def test_google_picture_is_used_when_nothing_was_uploaded() -> None:
    """It is still not deletable: it belongs to the Google account, not to this app."""
    user = _make_user(avatar_url="https://lh3.googleusercontent.com/a/pic")

    result = build_user_read(user, settings)

    assert result.avatar_url == "https://lh3.googleusercontent.com/a/pic"
    assert not result.has_uploaded_avatar


def test_avatar_url_is_null_when_there_is_no_avatar_at_all() -> None:
    assert build_user_read(_make_user(), settings).avatar_url is None


# --- PATCH /users/me -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_patch_updates_only_the_fields_the_body_carried() -> None:
    user = _make_user(bio="Original bio")
    service, repository, _ = _make_service(user)

    result = await update_current_user(
        UserUpdate.model_validate({"username": "Renamed"}), user, service
    )

    assert repository.updates == [{"username": "Renamed"}]
    assert result.username == "Renamed"
    assert result.bio == "Original bio"


@pytest.mark.asyncio
async def test_patch_with_an_explicit_null_bio_clears_it() -> None:
    user = _make_user(bio="Original bio")
    service, _, _ = _make_service(user)

    result = await update_current_user(UserUpdate.model_validate({"bio": None}), user, service)

    assert result.bio is None


@pytest.mark.asyncio
async def test_patch_with_an_empty_body_writes_nothing() -> None:
    user = _make_user()
    service, repository, _ = _make_service(user)

    await update_current_user(UserUpdate.model_validate({}), user, service)

    assert repository.updates == []


def test_a_blank_username_is_rejected() -> None:
    with pytest.raises(ValueError, match="blank"):
        UserUpdate.model_validate({"username": "   "})


def test_a_blank_bio_means_clear_it_rather_than_an_error() -> None:
    assert UserUpdate.model_validate({"bio": "   "}).bio is None


def test_a_bio_over_the_limit_is_rejected() -> None:
    with pytest.raises(ValueError, match="at most 300"):
        UserUpdate.model_validate({"bio": "x" * 301})


# --- avatar routes ---------------------------------------------------------------------


@pytest.mark.asyncio
async def test_uploading_an_avatar_stores_it_and_reaps_the_previous_one() -> None:
    user = _make_user()
    service, _, storage = _make_service(user)

    await upload_current_user_avatar(user, service, StubUpload(PNG_BYTES))  # type: ignore[arg-type]
    first_file_id = user.avatar_file_id
    await upload_current_user_avatar(user, service, StubUpload(JPEG_BYTES, "image/jpeg"))  # type: ignore[arg-type]

    assert first_file_id is not None
    assert user.avatar_file_id != first_file_id
    assert list(storage.files) == [user.avatar_file_id]


@pytest.mark.asyncio
async def test_upload_rejects_a_file_that_is_not_an_image() -> None:
    user = _make_user()
    service, _, _ = _make_service(user)

    with pytest.raises(HTTPException) as raised:
        await upload_current_user_avatar(  # type: ignore[arg-type]
            user, service, StubUpload(b"plain text", "text/plain")
        )

    assert raised.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.asyncio
async def test_upload_stops_reading_once_the_body_is_over_the_limit() -> None:
    """The cap has to bite while streaming: a client's Content-Length is not evidence,
    and buffering the whole body first is exactly what the limit exists to prevent."""
    user = _make_user()
    service, _, _ = _make_service(user)
    oversized = StubUpload(PNG_BYTES + b"\x00" * (settings.max_avatar_bytes + 1))

    with pytest.raises(HTTPException) as raised:
        await upload_current_user_avatar(user, service, oversized)  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_413_CONTENT_TOO_LARGE


@pytest.mark.asyncio
async def test_upload_surfaces_a_drive_outage_as_bad_gateway() -> None:
    """Google being down is not this server erroring -- a 500 here would be misleading
    and would put a traceback in front of the client."""
    user = _make_user()
    service = FailingUserService(AvatarStorageUnavailableError("Drive is unreachable"))

    with pytest.raises(HTTPException) as raised:
        await upload_current_user_avatar(user, service, StubUpload(PNG_BYTES))  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_502_BAD_GATEWAY


@pytest.mark.asyncio
async def test_deleting_an_avatar_clears_the_file_and_the_column() -> None:
    user = _make_user()
    service, _, storage = _make_service(user)
    await upload_current_user_avatar(user, service, StubUpload(PNG_BYTES))  # type: ignore[arg-type]

    result = await delete_current_user_avatar(user, service)

    assert user.avatar_file_id is None
    assert result.avatar_url is None
    assert storage.files == {}


@pytest.mark.asyncio
async def test_deleting_an_avatar_that_was_never_uploaded_is_a_404() -> None:
    user = _make_user()
    service, _, _ = _make_service(user)

    with pytest.raises(HTTPException) as raised:
        await delete_current_user_avatar(user, service)

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_reading_an_avatar_returns_the_bytes_marked_cacheable_forever() -> None:
    user = _make_user()
    service, _, _ = _make_service(user)
    await upload_current_user_avatar(user, service, StubUpload(PNG_BYTES))  # type: ignore[arg-type]

    response = await read_user_avatar("user-1", service)

    assert response.body == PNG_BYTES
    assert response.media_type == "image/png"
    assert "immutable" in response.headers["cache-control"]


@pytest.mark.asyncio
async def test_reading_the_avatar_of_a_user_who_has_none_is_a_404() -> None:
    service = FailingUserService(AvatarNotFoundError("no avatar"))

    with pytest.raises(HTTPException) as raised:
        await read_user_avatar("user-1", service)  # type: ignore[arg-type]

    assert raised.value.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
async def test_read_current_user_exposes_the_profile_fields() -> None:
    user = _make_user(bio="Learning Vietnamese")

    result = await read_current_user(user)

    assert result.bio == "Learning Vietnamese"
    assert result.username == "Player"
