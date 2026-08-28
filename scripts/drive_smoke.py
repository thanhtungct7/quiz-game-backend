"""Check that avatar storage is actually usable before trusting it in the app.

Exercises the whole Google Drive path end to end -- refresh the token, upload a
1x1 PNG into the configured folder, read it back, delete it -- and, when something
fails, says which account the token belongs to and what rights that account has on
the folder. That last part matters: the most common failure is approving the OAuth
consent with a different Google account than the one that owns the folder, which
surfaces only as a generic 403.

Leaves nothing behind on success.

Usage:
    conda run -n backend python -m scripts.drive_smoke
"""

import asyncio
import json

import httpx

from app.core.config import settings
from app.services.auth.avatar_storage import GoogleDriveAvatarStorage, validate_avatar

# Smallest valid PNG: 1x1, fully transparent.
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06"
    b"\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00"
    b"\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)


async def _report_identity(storage: GoogleDriveAvatarStorage, token: str) -> None:
    headers = {"Authorization": f"Bearer {token}"}
    folder_id = settings.google_drive_avatar_folder_id
    async with httpx.AsyncClient(timeout=20) as client:
        info = await client.get(
            "https://oauth2.googleapis.com/tokeninfo", params={"access_token": token}
        )
        print("  granted scopes:", info.json().get("scope"))

        about = await client.get(
            "https://www.googleapis.com/drive/v3/about",
            params={"fields": "user"},
            headers=headers,
        )
        print("  token account :", about.json().get("user", {}).get("emailAddress"))

        folder = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{folder_id}",
            params={
                "fields": "id,name,owners(emailAddress),"
                "capabilities(canAddChildren,canEdit)"
            },
            headers=headers,
        )
        print("  folder        :", json.dumps(folder.json()))


async def main() -> int:
    if not settings.is_avatar_storage_configured:
        print("GOOGLE_DRIVE_CLIENT_ID / _CLIENT_SECRET / _REFRESH_TOKEN are not set in .env.")
        print("Run: python -m scripts.google_drive_authorize <client_secret.json>")
        return 2

    print("folder id:", settings.google_drive_avatar_folder_id)
    storage = GoogleDriveAvatarStorage(settings)
    token = ""
    try:
        media_type = validate_avatar(PNG, "image/png", settings.max_avatar_bytes)
        print("validate : OK ->", media_type)

        token = await storage._bearer_token()  # noqa: SLF001 (diagnostics, not production use)
        print("oauth    : refresh OK")

        file_id = await storage.upload(PNG, media_type, "smoketest")
        print("upload   : OK ->", file_id)

        content, downloaded_type = await storage.download(file_id)
        print(f"download : OK -> {len(content)} bytes, {downloaded_type}")
        if content != PNG:
            print("download : MISMATCH -- bytes came back different")
            return 1

        await storage.delete(file_id)
        print("delete   : OK")
        print("\nAvatar storage is ready.")
        return 0
    except Exception as exc:
        print(f"\nFAILED: {type(exc).__name__}: {exc}\n")
        if token:
            print("Diagnostics:")
            await _report_identity(storage, token)
            print(
                "\nIf 'token account' is not the folder's owner and canAddChildren is false,"
                "\nre-run scripts.google_drive_authorize and approve with the owning account."
            )
        return 1
    finally:
        await storage.aclose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
