"""Bundle the vocabulary pictures into the Android app as WebP.

Firebase Storage serves them from us-east1, about a second per picture from
Vietnam -- too slow for a question on a clock. The app therefore ships every
picture in its assets and falls back to Firebase only for one it lacks.
`asset_path` here and `bundledImageAsset` in the app's MediaUrl.kt must agree.

Reads the originals from the unzipped deck, using the vocab JSON written by
scripts.convert. A picture already exported is kept unless --force is given,
and a WebP no word references any more is deleted.

Needs Pillow, which the backend environment does not carry:

    python3 -m scripts.export_vocab_images [--quality 65] [--force]
"""

import argparse
import json
from pathlib import Path

from scripts.convert import DEFAULT_OUT, DEFAULT_SRC, media_items

DEFAULT_ASSETS = (
    Path(__file__).resolve().parents[2] / "duo-game-app" / "app" / "src" / "main" / "assets"
)
IMAGE_PREFIX = "vocab/images/"
# 65 keeps the 390x260 pictures sharp on a phone at about 36 MB for the set;
# 80 barely shrinks the source JPEGs at all.
DEFAULT_QUALITY = 65


def asset_path(path: str) -> str | None:
    """`vocab/images/01_0001.jpg` -> `vocab/images/01_0001.webp`; None for anything but a picture."""
    if not path.startswith(IMAGE_PREFIX):
        return None
    return path.rsplit(".", 1)[0] + ".webp"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--vocab", default=DEFAULT_OUT, help="JSON written by scripts.convert")
    parser.add_argument("--src", default=DEFAULT_SRC, help="unzipped .apkg folder")
    parser.add_argument("--assets", default=str(DEFAULT_ASSETS), help="the app's assets folder")
    parser.add_argument("--quality", type=int, default=DEFAULT_QUALITY)
    parser.add_argument("--force", action="store_true", help="re-encode pictures already exported")
    args = parser.parse_args()

    from PIL import Image  # only this script needs Pillow

    with open(args.vocab, encoding="utf-8") as f:
        entries = json.load(f)
    images = {
        item["path"]: item["anki_file"]
        for entry in entries
        for item in media_items(entry)
        if asset_path(item["path"])
    }

    assets = Path(args.assets)
    written = kept = total_bytes = 0
    for path, anki_file in sorted(images.items()):
        target = assets / str(asset_path(path))
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and not args.force:
            kept += 1
        else:
            with Image.open(Path(args.src) / anki_file) as picture:
                picture.convert("RGB").save(target, "WEBP", quality=args.quality, method=6)
            written += 1
        total_bytes += target.stat().st_size

    expected = {Path(str(asset_path(path))).name for path in images}
    stale = [file for file in (assets / IMAGE_PREFIX).glob("*.webp") if file.name not in expected]
    for file in stale:
        file.unlink()

    print(
        f"{len(images)} pictures -> {assets / IMAGE_PREFIX}: {written} written, {kept} kept, "
        f"{len(stale)} stale removed, {total_bytes / 1048576:.1f} MB"
    )


if __name__ == "__main__":
    main()
