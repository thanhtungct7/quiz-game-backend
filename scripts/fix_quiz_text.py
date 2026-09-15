"""Repair the quiz text an earlier import wrote, in place.

scripts/import_quiz_bank.py now cleans what it imports (see quiz_text_cleanup),
but a database imported before that still holds "hav e" and "is hers .".
Re-importing with --reset would fix the text by deleting the course, and with
it every learner's progress, their lesson battles and the questions of past duo
matches. This rewrites the text on the rows that already exist instead: the
same challenge, option and passage ids, so nothing pointing at them notices.

A value is only rewritten while it still reads exactly as the import wrote it,
so an edit made since -- through the admin API, say -- is left alone and
counted as skipped.

Dry run by default; --apply writes everything in one transaction.

Usage:
    conda run -n backend python -m scripts.fix_quiz_text [--apply]
"""

from __future__ import annotations

import argparse
import asyncio
from collections import defaultdict
from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import AsyncSessionFactory, engine
from app.models.content.challenge import Challenge
from app.models.content.challenge_option import ChallengeOption
from app.models.content.passage import Passage
from scripts.import_quiz_bank import (
    correct_text_of,
    load_json,
    transform_multiple_choice_4options,
    transform_reading_comprehension,
)

QUERY_CHUNK = 2000
SAMPLES_TO_PRINT = 12

Item = tuple[dict[str, Any], list[dict[str, Any]]]


@dataclass
class Repairs:
    challenges: list[dict[str, Any]] = field(default_factory=list)
    options: list[dict[str, Any]] = field(default_factory=list)
    passages: list[dict[str, Any]] = field(default_factory=list)
    # Already reads as it should: an earlier --apply, or an import that cleaned.
    already_clean: int = 0
    # Reads as neither: edited since the import, and left alone.
    skipped: int = 0
    samples: list[str] = field(default_factory=list)

    def note(self, where: str, before: str | None, after: str | None) -> None:
        if len(self.samples) < SAMPLES_TO_PRINT:
            self.samples.append(f"  {where}: {before!r} -> {after!r}")


def _chunks(values: Sequence[str], size: int) -> Iterator[Sequence[str]]:
    for start in range(0, len(values), size):
        yield values[start : start + size]


def _challenge_fields(item: Item) -> dict[str, str | None]:
    fields, option_dicts = item
    return {
        "question": fields["question"],
        "explanation": fields["explanation"],
        "correct_text": correct_text_of(option_dicts),
    }


async def _repair_challenges(
    session: AsyncSession, pairs: dict[str, tuple[Item, Item]], repairs: Repairs
) -> None:
    """`pairs` maps a source_ref to (as imported, as it should read)."""
    refs = list(pairs)
    for chunk in _chunks(refs, QUERY_CHUNK):
        rows = (
            await session.execute(
                select(
                    Challenge.id,
                    Challenge.source_ref,
                    Challenge.question,
                    Challenge.explanation,
                    Challenge.correct_text,
                ).where(Challenge.source_ref.in_(chunk))
            )
        ).all()
        options_by_challenge: dict[str, dict[int, Any]] = defaultdict(dict)
        if rows:
            option_rows = (
                await session.execute(
                    select(
                        ChallengeOption.id,
                        ChallengeOption.challenge_id,
                        ChallengeOption.order_index,
                        ChallengeOption.text,
                    ).where(ChallengeOption.challenge_id.in_([row.id for row in rows]))
                )
            ).all()
            for option in option_rows:
                options_by_challenge[option.challenge_id][option.order_index] = option

        for row in rows:
            raw, clean = pairs[row.source_ref]
            raw_fields, clean_fields = _challenge_fields(raw), _challenge_fields(clean)
            change: dict[str, Any] = {}
            for name, clean_value in clean_fields.items():
                if clean_value == raw_fields[name]:
                    continue
                if getattr(row, name) == clean_value:
                    repairs.already_clean += 1
                    continue
                if getattr(row, name) != raw_fields[name]:
                    repairs.skipped += 1
                    continue
                change[name] = clean_value
                repairs.note(f"{row.source_ref} {name}", raw_fields[name], clean_value)
            if change:
                repairs.challenges.append({"id": row.id, **change})

            # The import numbered options 1..n in the order the transform yields them.
            for index, (raw_option, clean_option) in enumerate(
                zip(raw[1], clean[1], strict=True), start=1
            ):
                if clean_option["text"] == raw_option["text"]:
                    continue
                existing = options_by_challenge[row.id].get(index)
                if existing is not None and existing.text == clean_option["text"]:
                    repairs.already_clean += 1
                    continue
                if existing is None or existing.text != raw_option["text"]:
                    repairs.skipped += 1
                    continue
                repairs.options.append({"id": existing.id, "text": clean_option["text"]})
                repairs.note(
                    f"{row.source_ref} option {index}", raw_option["text"], clean_option["text"]
                )


async def _repair_passages(
    session: AsyncSession, contents: dict[str, tuple[str, str]], repairs: Repairs
) -> None:
    """`contents` maps a passage source_ref to (as imported, as it should read)."""
    for chunk in _chunks(list(contents), QUERY_CHUNK):
        rows = (
            await session.execute(
                select(Passage.id, Passage.source_ref, Passage.content).where(
                    Passage.source_ref.in_(chunk)
                )
            )
        ).all()
        for row in rows:
            raw, clean = contents[row.source_ref]
            if row.content == clean:
                repairs.already_clean += 1
                continue
            if row.content != raw:
                repairs.skipped += 1
                continue
            repairs.passages.append({"id": row.id, "content": clean})
            repairs.note(f"passage {row.source_ref}", _excerpt(raw, clean), None)


def _excerpt(raw: str, clean: str) -> str:
    """The first stretch of an article that changed, for the dry-run listing."""
    start = next(
        (i for i, (a, b) in enumerate(zip(raw, clean, strict=False)) if a != b), len(clean)
    )
    return raw[max(0, start - 30) : start + 30].replace("\n", " ")


async def _collect(session: AsyncSession, repairs: Repairs) -> None:
    rows = load_json("quiz_multiple_choice_4options.json")
    pairs = {
        clean[0]["source_ref"]: (raw, clean)
        for raw, clean in zip(
            transform_multiple_choice_4options(rows, clean=False),
            transform_multiple_choice_4options(rows),
            strict=True,
        )
        if raw != clean
    }
    del rows
    print(f"  multiple choice: {len(pairs)} question(s) differ from the source")
    await _repair_challenges(session, pairs, repairs)

    for part in range(1, 6):
        rows = load_json(f"quiz_reading_comprehension_part{part}.json")
        pairs = {}
        contents: dict[str, tuple[str, str]] = {}
        for raw_item, clean_item in zip(
            transform_reading_comprehension(rows, clean=False),
            transform_reading_comprehension(rows),
            strict=True,
        ):
            raw_fields, raw_options, raw_passage = raw_item
            clean_fields, clean_options, clean_passage = clean_item
            if (raw_fields, raw_options) != (clean_fields, clean_options):
                pairs[clean_fields["source_ref"]] = (
                    (raw_fields, raw_options),
                    (clean_fields, clean_options),
                )
            if raw_passage["content"] != clean_passage["content"]:
                contents[clean_passage["source_ref"]] = (
                    raw_passage["content"],
                    clean_passage["content"],
                )
        del rows
        print(
            f"  reading part {part}: {len(pairs)} question(s), "
            f"{len(contents)} article(s) differ from the source"
        )
        await _repair_challenges(session, pairs, repairs)
        await _repair_passages(session, contents, repairs)


async def main(apply: bool) -> None:
    async with AsyncSessionFactory() as session:
        repairs = Repairs()
        print("Comparing the source files with what the import wrote...")
        await _collect(session, repairs)

        print(
            f"\n{len(repairs.challenges)} challenge(s), {len(repairs.options)} option(s) and "
            f"{len(repairs.passages)} passage(s) to rewrite; {repairs.already_clean} value(s) "
            f"already clean; {repairs.skipped} value(s) skipped because they were edited "
            "since the import."
        )
        if repairs.samples:
            print("For example:")
            print("\n".join(repairs.samples))

        if not apply:
            print("\nDry run: nothing written. Pass --apply to write.")
        else:
            if repairs.challenges:
                await session.execute(update(Challenge), repairs.challenges)
            if repairs.options:
                await session.execute(update(ChallengeOption), repairs.options)
            if repairs.passages:
                await session.execute(update(Passage), repairs.passages)
            await session.commit()
            print("\nWritten.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Write the repairs (default: dry run)."
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
