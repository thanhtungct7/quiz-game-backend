"""Mint the long-lived Google Drive refresh token the avatar storage runs on.

Run once, by hand, on a machine with a browser. It opens Google's consent screen,
you approve with the account that owns the avatar folder, and it prints the refresh
token to paste into .env.

Before running:
  1. Enable the Google Drive API on the Cloud project.
  2. Create an OAuth client of type "Desktop app" and download its client secret JSON.
  3. Set the OAuth consent screen's publishing status to "In production". While it is
     still "Testing", Google expires refresh tokens after 7 days and the server will
     start failing uploads a week after this script last ran.

The scope is full `drive`, not the narrower `drive.file`: `drive.file` only reaches
files the app itself created, so uploading into a folder that already exists (the one
in GOOGLE_DRIVE_AVATAR_FOLDER_ID) would fail with 404 on the parent.

Usage:
    conda run -n backend python -m scripts.google_drive_authorize path/to/client_secret.json
"""

import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/drive"]


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    secrets_path = Path(sys.argv[1])
    if not secrets_path.is_file():
        print(f"No such client secret file: {secrets_path}")
        return 2

    flow = InstalledAppFlow.from_client_secrets_file(str(secrets_path), SCOPES)
    # access_type=offline + prompt=consent is what makes Google hand back a refresh
    # token; without prompt=consent a re-authorisation returns only an access token.
    credentials = flow.run_local_server(
        port=0, access_type="offline", prompt="consent", open_browser=True
    )

    if not credentials.refresh_token:
        print("Google returned no refresh token. Revoke this app's access at")
        print("https://myaccount.google.com/permissions and run this again.")
        return 1

    print("\nAdd these to duo-game-back/.env:\n")
    print(f"GOOGLE_DRIVE_CLIENT_ID={credentials.client_id}")
    print(f"GOOGLE_DRIVE_CLIENT_SECRET={credentials.client_secret}")
    print(f"GOOGLE_DRIVE_REFRESH_TOKEN={credentials.refresh_token}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
