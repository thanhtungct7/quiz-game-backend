"""Delete the throwaway accounts the smoke scripts leave behind.

`scripts/duo_smoke.py`, `scripts/pve_smoke.py` and `scripts/drive_smoke.py`
each register users they never remove -- `duo-smoke-<name>-<hex>@example.com`
with the username `smoke-<name>` -- so a local database slowly fills with them
and they sit on the leaderboard, which is what a demo puts on screen.

Safe to run because of how the schema is drawn: every table that hangs off a
user is `ON DELETE CASCADE` (progress, game profile, duo rating, achievements,
gold, device tokens, benchmark attempts...), so deleting the account takes its
rows with it in one statement. The one exception is `duo_matches.winner_id`,
which is `SET NULL` -- a match the *other* player still owns survives with no
winner rather than blocking the delete.

Matches on the email, not the username: the address is what the scripts
generate, it is unique, and it is always `@example.com`, while a username is
free text a real account could also hold.

Dry run by default; --apply writes.

Usage:
    conda run -n backend python -m scripts.cleanup_smoke_users [--apply]
"""

from __future__ import annotations

import argparse
import asyncio

from sqlalchemy import delete, select

from app.db.session import AsyncSessionFactory, engine
from app.models.auth.user import User

# Covers every prefix the smoke scripts use (duo-, pve-, drive-) in one pattern,
# and cannot reach an address outside the reserved example.com domain.
EMAIL_PATTERN = "%smoke%@example.com"
SAMPLES_TO_PRINT = 10


async def main(apply: bool) -> None:
    async with AsyncSessionFactory() as session:
        rows = (
            await session.execute(
                select(User.id, User.email, User.username)
                .where(User.email.like(EMAIL_PATTERN))
                .order_by(User.email)
            )
        ).all()

        print(f"{len(rows)} account(s) match {EMAIL_PATTERN!r}.")
        for row in rows[:SAMPLES_TO_PRINT]:
            print(f"  {row.email}  (username {row.username!r})")
        if len(rows) > SAMPLES_TO_PRINT:
            print(f"  ... and {len(rows) - SAMPLES_TO_PRINT} more.")

        if not rows:
            pass
        elif not apply:
            print("\nDry run: nothing written. Pass --apply to delete them.")
        else:
            ids = [row.id for row in rows]
            await session.execute(delete(User).where(User.id.in_(ids)))
            await session.commit()
            print(f"\nDeleted {len(ids)} account(s) and everything cascading from them.")

    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="Delete the accounts (default: dry run)."
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.apply))
