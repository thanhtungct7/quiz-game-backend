"""Give the vocab media already in Firebase Storage readable paths.

The deck's media was uploaded to the bucket root under Anki's numeric names
("2909"), with no extension and as application/octet-stream. This puts every
file the vocab JSON references at the path `scripts.convert` gave it
(`vocab/images/01_0001.jpg`), with its real content type and a year-long
Cache-Control -- a file name never changes content, so a device can keep it.

Files already in the bucket are copied with server-side rewrites: nothing is
re-uploaded, and the numeric originals are left where they are. A file that
never made it into the bucket is uploaded from the unzipped deck instead. A
path that already holds the right size and headers is skipped, so an
interrupted run can simply be started again.

    python -m scripts.firebase_vocab_media            # dry run: what would change
    python -m scripts.firebase_vocab_media --apply

The app reads these files without signing in, which needs a Storage rule:

    match /vocab/{allPaths=**} {
      allow read: if true;
      allow write: if false;
    }
"""

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import firebase_admin
from firebase_admin import credentials, storage

from app.core.config import settings
from scripts.convert import DEFAULT_OUT, DEFAULT_SRC, STORAGE_PREFIX, content_type_of, media_items

DEFAULT_BUCKET = "duo-d298a.firebasestorage.app"
CACHE_CONTROL = "public, max-age=31536000, immutable"
WORKERS = 16


def copy_plan(entries: list[dict[str, Any]]) -> dict[str, str]:
    """Storage path -> the numeric Anki file it holds."""
    plan: dict[str, str] = {}
    for entry in entries:
        for item in media_items(entry):
            plan[item["path"]] = item["anki_file"]
    return plan


def is_in_place(blob: Any, size: int) -> bool:
    return (
        blob is not None
        and blob.size == size
        and blob.content_type == content_type_of(blob.name)
        and blob.cache_control == CACHE_CONTROL
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--vocab", default=DEFAULT_OUT, help="JSON written by scripts.convert")
    parser.add_argument(
        "--src",
        default=DEFAULT_SRC,
        help="unzipped .apkg folder, for files missing from the bucket",
    )
    parser.add_argument("--bucket", default=DEFAULT_BUCKET)
    parser.add_argument("--apply", action="store_true", help="write for real (default: dry run)")
    args = parser.parse_args()

    if not settings.firebase_credentials_file:
        raise SystemExit("FIREBASE_CREDENTIALS_FILE is not set")
    firebase_admin.initialize_app(
        credentials.Certificate(settings.firebase_credentials_file), {"storageBucket": args.bucket}
    )
    bucket = storage.bucket()

    with open(args.vocab, encoding="utf-8") as f:
        plan = copy_plan(json.load(f))

    # One listing instead of two lookups per file.
    blobs = {blob.name: blob for blob in bucket.list_blobs()}
    expected_size: dict[str, int] = {}
    copies: dict[str, str] = {}  # path -> bucket object
    uploads: dict[str, str] = {}  # path -> local deck file
    unavailable: list[str] = []
    for path, anki_file in plan.items():
        if anki_file in blobs:
            expected_size[path] = blobs[anki_file].size
            if not is_in_place(blobs.get(path), expected_size[path]):
                copies[path] = anki_file
            continue
        local = os.path.join(args.src, anki_file)
        if not os.path.exists(local):
            unavailable.append(path)
            continue
        expected_size[path] = os.path.getsize(local)
        if not is_in_place(blobs.get(path), expected_size[path]):
            uploads[path] = local

    in_place = len(plan) - len(copies) - len(uploads) - len(unavailable)
    print(
        f"{len(plan)} media paths: {in_place} already in place, {len(copies)} to copy inside "
        f"the bucket, {len(uploads)} to upload (missing from the bucket), "
        f"{len(unavailable)} unavailable anywhere"
    )
    for path, anki_file in list(copies.items())[:5]:
        print(f"  copy   {anki_file} -> {path} ({content_type_of(path)})")
    for path, local in uploads.items():
        print(f"  upload {local} -> {path} ({content_type_of(path)})")
    if unavailable:
        print(f"  unavailable, first: {unavailable[:10]}")

    if not args.apply:
        print("Dry run. Pass --apply to write.")
        return

    def place(path: str) -> None:
        target = bucket.blob(path)
        # Both rewrite() and the upload send the destination's properties, so
        # the file is created with these headers instead of octet-stream.
        target.content_type = content_type_of(path)
        target.cache_control = CACHE_CONTROL
        if path in uploads:
            target.upload_from_filename(uploads[path], content_type=target.content_type)
            return
        source = blobs[copies[path]]
        token, _, _ = target.rewrite(source)
        while token is not None:
            token, _, _ = target.rewrite(source, token=token)

    todo = [*copies, *uploads]
    failed: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        futures = {pool.submit(place, path): path for path in todo}
        for done, future in enumerate(as_completed(futures), start=1):
            try:
                future.result()
            except Exception as exc:  # noqa: BLE001 -- report every failure, then carry on
                failed.append((futures[future], repr(exc)))
            if done % 1000 == 0:
                print(f"  {done}/{len(todo)}", flush=True)

    placed = {blob.name: blob for blob in bucket.list_blobs(prefix=f"{STORAGE_PREFIX}/")}
    wrong = [
        path for path, size in expected_size.items() if not is_in_place(placed.get(path), size)
    ]
    types = Counter(placed[path].content_type for path in expected_size if path in placed)
    print(f"Wrote {len(todo) - len(failed)}, failed {len(failed)}.")
    print(
        f"Verified: {len(expected_size) - len(wrong)}/{len(plan)} paths in place; "
        f"content types {dict(types)}"
    )
    for path, error in failed[:10]:
        print(f"  failed {path}: {error}")


if __name__ == "__main__":
    main()
