"""Avatar blob storage.

Avatars are held in a folder of the maintainer's own Google Drive. Two consequences shape
this module:

* The credentials are an OAuth *user* refresh token, not a service account. A service
  account owns no My Drive quota, so every upload into a personal folder would fail with
  `storageQuotaExceeded`; a refresh token makes the maintainer the file owner instead.
* Drive's public link formats (`drive.google.com/uc?...`, `lh3.googleusercontent.com/d/...`)
  are undocumented and throttled, so nothing outside this module ever sees them. Callers get
  a file id and read the bytes back through our own API -- see the avatar route.

The Drive REST API is called directly over httpx rather than through
`google-api-python-client`, which is blocking and would need a threadpool hop on every call
in an otherwise fully async stack.
"""

import asyncio
import json
import logging
import time
from typing import Protocol
from uuid import uuid4

import httpx

from app.core.config import Settings
from app.core.exceptions import (
    AvatarNotFoundError,
    AvatarStorageUnavailableError,
    AvatarTooLargeError,
    InvalidAvatarError,
)

logger = logging.getLogger(__name__)

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 (endpoint, not a secret)
DRIVE_FILES_URL = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"

ALLOWED_AVATAR_TYPES = {"image/jpeg", "image/png", "image/webp"}
_EXTENSIONS = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp"}

# Sniffed from the bytes themselves: a client is free to label a .exe as image/png.
_MAGIC_PREFIXES: tuple[tuple[bytes, str], ...] = (
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)


def detect_image_type(content: bytes) -> str | None:
    """Return the media type the bytes actually are, or None if they are not an image
    this app accepts."""
    for prefix, media_type in _MAGIC_PREFIXES:
        if content.startswith(prefix):
            return media_type
    # WEBP is "RIFF" + 4 size bytes + "WEBP", so the marker is not a plain prefix.
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "image/webp"
    return None


def validate_avatar(content: bytes, declared_type: str | None, max_bytes: int) -> str:
    """Check an upload and return the media type to store it under.

    The declared `Content-Type` is only used to reject early; the stored type always comes
    from the bytes, so a mislabelled file can never be served back under the wrong type.
    """
    if not content:
        raise InvalidAvatarError("The uploaded file is empty")
    if len(content) > max_bytes:
        raise AvatarTooLargeError(f"Avatar must be at most {max_bytes // 1024} KB")
    if (
        declared_type is not None
        and declared_type.split(";")[0].strip() not in ALLOWED_AVATAR_TYPES
    ):
        raise InvalidAvatarError("Avatar must be a JPEG, PNG or WEBP image")

    actual_type = detect_image_type(content)
    if actual_type is None:
        raise InvalidAvatarError("Avatar must be a JPEG, PNG or WEBP image")
    return actual_type


class AvatarStorage(Protocol):
    async def upload(self, content: bytes, content_type: str, user_id: str) -> str:
        """Store the bytes and return the id they can be read back with."""

    async def download(self, file_id: str) -> tuple[bytes, str]:
        """Return the stored bytes and their media type."""

    async def delete(self, file_id: str) -> None:
        """Remove the stored bytes. Deleting an unknown id is not an error."""


class GoogleDriveAvatarStorage:
    def __init__(self, config: Settings) -> None:
        self._config = config
        self._client: httpx.AsyncClient | None = None
        self._access_token: str | None = None
        self._access_token_expires_at = 0.0
        # One refresh at a time: a burst of uploads on a cold cache would otherwise each
        # spend a roundtrip minting a token that the others already have in flight.
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=httpx.Timeout(20.0, connect=10.0))
        return self._client

    async def _bearer_token(self) -> str:
        async with self._token_lock:
            if self._access_token and time.monotonic() < self._access_token_expires_at:
                return self._access_token

            client_id = self._config.google_drive_client_id
            client_secret = self._config.google_drive_client_secret
            refresh_token = self._config.google_drive_refresh_token
            if client_id is None or client_secret is None or refresh_token is None:
                raise AvatarStorageUnavailableError("Avatar storage is not configured")

            try:
                response = await self._http().post(
                    OAUTH_TOKEN_URL,
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret.get_secret_value(),
                        "refresh_token": refresh_token.get_secret_value(),
                        "grant_type": "refresh_token",
                    },
                )
            except httpx.HTTPError as exc:
                raise AvatarStorageUnavailableError("Could not reach Google OAuth") from exc

            if response.status_code != httpx.codes.OK:
                # A revoked token, a rotated client secret, or a refresh token that expired
                # because the OAuth consent screen is still in "Testing" all land here.
                logger.error(
                    "Google Drive token refresh failed status=%s body=%s",
                    response.status_code,
                    response.text[:500],
                )
                raise AvatarStorageUnavailableError("Avatar storage credentials were rejected")

            payload = response.json()
            token = payload.get("access_token")
            if not isinstance(token, str) or not token:
                raise AvatarStorageUnavailableError("Google OAuth returned no access token")

            expires_in = payload.get("expires_in", 3600)
            self._access_token = token
            self._access_token_expires_at = time.monotonic() + float(expires_in) - 60
            return token

    async def _authorized_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {await self._bearer_token()}"}

    async def upload(self, content: bytes, content_type: str, user_id: str) -> str:
        extension = _EXTENSIONS.get(content_type, "bin")
        metadata = {
            "name": f"avatar_{user_id}_{uuid4().hex}.{extension}",
            "parents": [self._config.google_drive_avatar_folder_id],
            # Kept so orphaned files can still be traced back to a user by hand.
            "appProperties": {"user_id": user_id},
        }
        boundary = uuid4().hex
        body = b"".join(
            (
                f"--{boundary}\r\nContent-Type: application/json; charset=UTF-8\r\n\r\n".encode(),
                json.dumps(metadata).encode(),
                f"\r\n--{boundary}\r\nContent-Type: {content_type}\r\n\r\n".encode(),
                content,
                f"\r\n--{boundary}--\r\n".encode(),
            )
        )
        headers = await self._authorized_headers()
        headers["Content-Type"] = f"multipart/related; boundary={boundary}"

        response = await self._request(
            "POST",
            DRIVE_UPLOAD_URL,
            params={"uploadType": "multipart", "fields": "id"},
            headers=headers,
            content=body,
        )
        file_id = response.json().get("id")
        if not isinstance(file_id, str) or not file_id:
            raise AvatarStorageUnavailableError("Google Drive returned no file id")
        return file_id

    async def download(self, file_id: str) -> tuple[bytes, str]:
        response = await self._request(
            "GET",
            f"{DRIVE_FILES_URL}/{file_id}",
            params={"alt": "media"},
            headers=await self._authorized_headers(),
        )
        content_type = response.headers.get("content-type", "application/octet-stream")
        return response.content, content_type.split(";")[0].strip()

    async def delete(self, file_id: str) -> None:
        try:
            await self._request(
                "DELETE",
                f"{DRIVE_FILES_URL}/{file_id}",
                headers=await self._authorized_headers(),
            )
        except AvatarNotFoundError:
            return

    async def _request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        params: dict[str, str] | None = None,
        content: bytes | None = None,
    ) -> httpx.Response:
        try:
            response = await self._http().request(
                method, url, headers=headers, params=params, content=content
            )
        except httpx.HTTPError as exc:
            raise AvatarStorageUnavailableError("Could not reach Google Drive") from exc

        if response.status_code == httpx.codes.NOT_FOUND:
            raise AvatarNotFoundError("Avatar is no longer stored")
        if response.status_code >= httpx.codes.BAD_REQUEST:
            logger.error(
                "Google Drive %s %s failed status=%s body=%s",
                method,
                url,
                response.status_code,
                response.text[:500],
            )
            raise AvatarStorageUnavailableError("Google Drive rejected the request")
        return response


class InMemoryAvatarStorage:
    """Test double, and the fallback in development when no Drive credentials are set."""

    def __init__(self) -> None:
        self.files: dict[str, tuple[bytes, str]] = {}

    async def upload(self, content: bytes, content_type: str, user_id: str) -> str:
        file_id = f"{user_id}-{uuid4().hex}"[:64]
        self.files[file_id] = (content, content_type)
        return file_id

    async def download(self, file_id: str) -> tuple[bytes, str]:
        stored = self.files.get(file_id)
        if stored is None:
            raise AvatarNotFoundError("Avatar is no longer stored")
        return stored

    async def delete(self, file_id: str) -> None:
        self.files.pop(file_id, None)
