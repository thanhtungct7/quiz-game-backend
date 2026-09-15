"""Map the "4000 Essential English Words" Anki export to words, images and audio.

The unzipped .apkg holds:
- `collection.anki21`: the real notes. `collection.anki2` next to it is a stub
  with one note ("Please update to the latest Anki version...") for old Anki
  versions, so it must not be read.
- `media`: JSON {"<numeric file>": "<original name>"}, e.g. {"2909": "01_0001.jpg"}.
- the media files themselves, named 0, 1, 2... with no extension.

The deck is English-English: there is no Vietnamese field. `meaning` is an
English definition.

Media lives in Firebase Storage. Each media item carries the storage `path` it
is served from (`vocab/images/01_0001.jpg`, the value challenge options store)
and the `anki_file` it was uploaded as, which `scripts.firebase_vocab_media`
copies to that path.

Usage:
    python -m scripts.convert [--src DIR] [--out FILE]
"""

import argparse
import html
import json
import os
import re
import sqlite3
from collections.abc import Iterator
from typing import Any

DEFAULT_SRC = "/home/trtung/Downloads/4000_Essential_English_Words_all_books_en-en"
DEFAULT_OUT = "data/vocab/vocab_4000.json"
STORAGE_PREFIX = "vocab"

SOUND_RE = re.compile(r"\[sound:(.*?)\]")
IMG_RE = re.compile(r"<img[^>]*?src=\"([^\"]+)\"", re.IGNORECASE)
TAG_RE = re.compile(r"<[^>]+>")
UNSAFE_NAME_RE = re.compile(r"[^A-Za-z0-9_.-]+")
# "28_2942.jpg": unit 28 of its book, word 2942 of the whole series.
NUMBERED_RE = re.compile(r"^(\d{2})_(\d{4})")

CONTENT_TYPES = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "svg": "image/svg+xml",
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
    "ogg": "audio/ogg",
}
IMAGE_EXTENSIONS = {"jpg", "jpeg", "png", "gif", "svg"}

# Anki note type name -> {output key: Anki field name}.
# "4000 EEW" is the 3600 words of Books 1-6; "4000 EEW Extra" is 271 picture words.
FIELD_MAP = {
    "4000 EEW": {
        "word": "Word",
        "image": "Image",
        "audio_word": "Sound",
        "audio_meaning": "Sound_Meaning",
        "audio_example": "Sound_Example",
        "meaning": "Meaning",
        "example": "Example",
        "ipa": "IPA",
    },
    "4000 EEW Extra": {
        "word": "English",
        "image": "IMG",
        "audio_word": "Audio",
        "ipa": "Am&BrTranscription",
    },
}
MEDIA_KEYS = ("image", "audio_word", "audio_meaning", "audio_example")


def _extension(name: str) -> str:
    return name.rsplit(".", 1)[-1].lower() if "." in name else ""


def storage_path(filename: str) -> str:
    """Where a deck file is served from: `vocab/images/01_0001.jpg`.

    Anything but letters, digits, `_`, `.` and `-` becomes `_`, so the Extra
    deck's "mountain lion_1397924728921.jpg" needs no escaping in a URL.
    """
    folder = "images" if _extension(filename) in IMAGE_EXTENSIONS else "audio"
    return f"{STORAGE_PREFIX}/{folder}/{UNSAFE_NAME_RE.sub('_', filename)}"


def content_type_of(path: str) -> str:
    return CONTENT_TYPES.get(_extension(path), "application/octet-stream")


def clean_text(value: str) -> str:
    value = SOUND_RE.sub("", value)
    value = re.sub(r"<br\s*/?>", " ", value, flags=re.IGNORECASE)
    value = TAG_RE.sub("", value)
    value = html.unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", value).strip()


def mask(value: str, tag: str) -> tuple[str | None, str | None]:
    """Blank out every `<tag>...</tag>` and return (masked text, what was blanked).

    The deck marks the headword in italics in its definition ("To <i>agree</i>
    is...") and in bold in its example ("They <b>arrived</b>..."), already in the
    inflected form the sentence uses. Both are (None, None) when the tag is absent.
    """
    pattern = re.compile(rf"<{tag}>(.*?)</{tag}>", re.IGNORECASE | re.DOTALL)
    answers = [clean_text(found) for found in pattern.findall(value)]
    if not answers:
        return None, None
    return clean_text(pattern.sub("___", value)), " ".join(answers)


def hide_word(text: str | None, word: str) -> str | None:
    """Blank out the headword wherever the italics missed it.

    "To <i>ride</i> something is to travel on it. You can ride an animal" would
    still give the answer away after `mask` alone.
    """
    if text is None:
        return None
    return re.sub(rf"(?<![A-Za-z]){re.escape(word)}(?![A-Za-z])", "___", text, flags=re.IGNORECASE)


def media_items(entry: dict[str, Any]) -> Iterator[dict[str, str]]:
    """Every media item of one word, extras included."""
    for key in MEDIA_KEYS:
        if entry.get(key):
            yield entry[key]
        yield from entry.get(f"{key}_extra", [])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--src", default=DEFAULT_SRC, help="unzipped .apkg folder")
    parser.add_argument("--out", default=DEFAULT_OUT, help="output JSON file")
    args = parser.parse_args()

    with open(os.path.join(args.src, "media"), encoding="utf-8") as f:
        num_to_name: dict[str, str] = json.load(f)
    name_to_num = {name: num for num, name in num_to_name.items()}

    db_path = os.path.join(args.src, "collection.anki21")
    if not os.path.exists(db_path):
        raise SystemExit(
            f"{db_path} not found. collection.anki2 is only a stub for old Anki; "
            "a newer export (collection.anki21b) is zstd-compressed and needs unpacking first."
        )
    conn = sqlite3.connect(db_path)
    col_models, col_decks = conn.execute("SELECT models, decks FROM col").fetchone()
    models = json.loads(col_models)
    decks = {int(did): deck["name"] for did, deck in json.loads(col_decks).items()}
    note_deck = dict(conn.execute("SELECT nid, MIN(did) FROM cards GROUP BY nid"))
    rows = conn.execute("SELECT id, mid, flds FROM notes ORDER BY sfld, id").fetchall()
    conn.close()

    result = []
    missing: list[tuple[int, str]] = []

    for note_id, mid, flds in rows:
        model = models[str(mid)]
        mapping = FIELD_MAP.get(model["name"])
        if mapping is None:
            raise SystemExit(f"note {note_id}: unknown note type {model['name']!r}")
        field_names = [fld["name"] for fld in sorted(model["flds"], key=lambda fld: fld["ord"])]
        fields = dict(zip(field_names, flds.split("\x1f")))

        def media(field: str, pattern: re.Pattern[str]) -> list[dict[str, str]]:
            files = []
            for name in pattern.findall(fields.get(field, "")):
                name = html.unescape(name)
                num = name_to_num.get(name)
                if num is None or not os.path.exists(os.path.join(args.src, num)):
                    missing.append((note_id, name))
                    continue
                files.append({"path": storage_path(name), "anki_file": num})
            return files

        ipa = clean_text(fields.get(mapping["ipa"], ""))
        # "[ɡreɪ]" -> "ɡreɪ", but keep "BrE [heə(r)] NAmE [her]" whole.
        if ipa.startswith("[") and ipa.endswith("]") and ipa.count("[") == 1:
            ipa = ipa[1:-1]

        raw_meaning = fields.get(mapping.get("meaning", ""), "")
        raw_example = fields.get(mapping.get("example", ""), "")
        word = clean_text(fields[mapping["word"]])
        meaning_masked, _ = mask(raw_meaning, "i")
        meaning_masked = hide_word(meaning_masked, word)
        example_masked, example_answer = mask(raw_example, "b")
        # "…put the dirty <b>laundry</b> in a basket" can name the word unbolded too.
        example_masked = hide_word(example_masked, word)

        deck = decks.get(note_deck.get(note_id), "").rsplit("::", 1)[-1]  # "1.Book" or "Extra"
        entry: dict[str, Any] = {
            "note_id": note_id,
            "book": int(deck.split(".")[0]) if deck[:1].isdigit() else None,
            "unit": None,
            "index": None,
            "word": word,
            "ipa": ipa,
            "meaning": clean_text(raw_meaning),
            "meaning_masked": meaning_masked,
            "example": clean_text(raw_example),
            "example_masked": example_masked,
            "example_answer": example_answer,
        }
        for key in MEDIA_KEYS:
            field = mapping.get(key)
            files = media(field, IMG_RE if key == "image" else SOUND_RE) if field else []
            entry[key] = files[0] if files else None
            if len(files) > 1:
                entry[f"{key}_extra"] = files[1:]

        numbered = NUMBERED_RE.match(entry["image"]["path"].rsplit("/", 1)[-1]) if entry["image"] else None
        if entry["book"] is not None and numbered:
            entry["unit"], entry["index"] = int(numbered.group(1)), int(numbered.group(2))
        result.append(entry)

    result.sort(key=lambda e: (e["book"] is None, e["index"] or 0, e["word"].lower()))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    paths = {item["path"] for entry in result for item in media_items(entry)}
    print(f"{len(result)} words -> {args.out}, {len(paths)} media files")
    print(
        f"  no masked meaning: {sum(1 for e in result if e['book'] and not e['meaning_masked'])}, "
        f"no masked example: {sum(1 for e in result if e['book'] and not e['example_masked'])}"
    )
    if missing:
        print(f"{len(missing)} referenced media files not found, first: {missing[:5]}")


if __name__ == "__main__":
    main()
