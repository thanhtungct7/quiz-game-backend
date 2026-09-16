"""Repair, in a database already imported, the questions that cannot be answered.

Two faults, both from a source file, both now handled at import by
scripts/quiz_option_repairs.py -- this brings an existing database in line
without re-importing, which would delete every learner's progress:

* ANSWER_KEY_FIXES -- the source's `correct_answer` named no option, so the
  import marked every option wrong and the learner could not answer at all.
  The intended answer is marked correct and `correct_text` is filled in.
* DROPPED_QUESTIONS -- nothing left to ask. Deleted, options and all.

A question is only touched while it still looks the way the import left it: an
answer key is set only on a question that really has no correct option, so an
edit made since through the admin API is left alone and counted as skipped.

Dry run by default; --apply writes everything in one transaction.

Usage:
    conda run -n backend python -m scripts.repair_broken_questions [--apply]
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import selectinload

from app.db.session import AsyncSessionFactory, engine
from app.models.content.challenge import Challenge
from app.models.content.challenge_option import ChallengeOption
from app.models.progress.user_challenge_progress import UserChallengeProgress
from scripts.quiz_option_repairs import ANSWER_KEY_FIXES, DROPPED_QUESTIONS

SAMPLES_TO_PRINT = 5


async def main(apply: bool) -> None:
    async with AsyncSessionFactory() as session:
        # --- answer keys -----------------------------------------------------
        refs = sorted(ANSWER_KEY_FIXES)
        challenges = (
            (
                await session.execute(
                    select(Challenge)
                    .options(selectinload(Challenge.options))
                    .where(Challenge.source_ref.in_(refs))
                )
            )
            .scalars()
            .all()
        )
        option_updates: list[dict[str, object]] = []
        challenge_updates: list[dict[str, object]] = []
        skipped = 0
        print(f"{len(refs)} question(s) listed with no answer; {len(challenges)} found.")
        for challenge in challenges:
            if any(option.correct for option in challenge.options):
                skipped += 1
                continue
            position = ANSWER_KEY_FIXES[challenge.source_ref or ""]
            options = sorted(challenge.options, key=lambda option: option.order_index)
            answer = options[position - 1]
            for option in options:
                option_updates.append({"id": option.id, "correct": option is answer})
            challenge_updates.append({"id": challenge.id, "correct_text": answer.text})
            print(f"  {challenge.source_ref}: answer -> {answer.text!r}")
        if skipped:
            print(f"  {skipped} already had an answer and were left alone.")

        # --- questions to delete ---------------------------------------------
        dropped = sorted(DROPPED_QUESTIONS)
        rows = (
            await session.execute(
                select(Challenge.id, Challenge.source_ref, Challenge.question).where(
                    Challenge.source_ref.in_(dropped)
                )
            )
        ).all()
        ids = [row.id for row in rows]
        print(f"\n{len(dropped)} question(s) listed as broken; {len(rows)} found.")
        if ids:
            options = await session.scalar(
                select(func.count())
                .select_from(ChallengeOption)
                .where(ChallengeOption.challenge_id.in_(ids))
            )
            progress = await session.scalar(
                select(func.count())
                .select_from(UserChallengeProgress)
                .where(UserChallengeProgress.challenge_id.in_(ids))
            )
            print(
                f"Deleting them takes {options} option(s) with them and discards "
                f"{progress} learner progress row(s)."
            )
            for row in rows[:SAMPLES_TO_PRINT]:
                print(f"  {row.source_ref}: {row.question[:90]!r}")

        if not apply:
            print("\nDry run: nothing written. Pass --apply to write.")
        else:
            if option_updates:
                await session.execute(update(ChallengeOption), option_updates)
            if challenge_updates:
                await session.execute(update(Challenge), challenge_updates)
            if ids:
                await session.execute(delete(Challenge).where(Challenge.id.in_(ids)))
            await session.commit()
            print(
                f"\nWritten: {len(challenge_updates)} answer key(s) set, "
                f"{len(ids)} challenge(s) deleted."
            )

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Write the repairs (default: dry run)."
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
