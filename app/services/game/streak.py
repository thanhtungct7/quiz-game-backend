"""The daily study streak, and the head start it buys in a match.

Pure functions with no I/O. `now` and `today` are always parameters.

Days are counted in the learner's own timezone, not UTC. With a UTC boundary a
Vietnamese student's day would roll over at 07:00 local time, breaking a streak
in the middle of a morning session.
"""

from datetime import UTC, date, datetime, timedelta, timezone

STREAK_TZ = timezone(timedelta(hours=7))


def today_in_streak_tz(now: datetime) -> date:
    """The calendar day `now` falls on, for streak purposes."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(STREAK_TZ).date()


def streak_after(current: int, last_active: date | None, today: date) -> int:
    """The streak once activity is recorded for `today`.

    Same day keeps it, the next day extends it, and any gap starts over at one.
    """
    if last_active is None:
        return 1
    if today == last_active:
        return max(1, current)
    if today == last_active + timedelta(days=1):
        return max(1, current) + 1
    return 1
