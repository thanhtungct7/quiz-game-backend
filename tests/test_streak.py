from datetime import UTC, date, datetime, timedelta

from app.services.game.streak import STREAK_TZ, streak_after, today_in_streak_tz

TODAY = date(2026, 8, 29)


def test_the_first_day_starts_a_streak() -> None:
    assert streak_after(0, None, TODAY) == 1


def test_more_activity_on_the_same_day_changes_nothing() -> None:
    assert streak_after(6, TODAY, TODAY) == 6


def test_the_next_day_extends_the_streak() -> None:
    assert streak_after(6, TODAY - timedelta(days=1), TODAY) == 7


def test_a_missed_day_starts_over() -> None:
    assert streak_after(30, TODAY - timedelta(days=2), TODAY) == 1
    assert streak_after(30, TODAY - timedelta(days=90), TODAY) == 1


def test_a_streak_is_never_zero_once_there_is_activity() -> None:
    # A stored zero alongside a recorded last-active day is inconsistent state
    # (a profile written before streaks existed). It is read as one rather than
    # left at zero, so recording today produces a real streak either way.
    assert streak_after(0, TODAY, TODAY) == 1
    assert streak_after(0, TODAY - timedelta(days=1), TODAY) == 2


def test_the_day_boundary_follows_the_learner_not_utc() -> None:
    # 23:30 in UTC+7 is still the same study day, though it is already
    # tomorrow in UTC+8 and still yesterday afternoon in UTC.
    late_evening = datetime(2026, 8, 29, 23, 30, tzinfo=STREAK_TZ)
    assert today_in_streak_tz(late_evening) == TODAY
    assert today_in_streak_tz(late_evening.astimezone(UTC)) == TODAY


def test_a_morning_session_is_not_yesterday() -> None:
    # 07:00 local is 00:00 UTC: with a UTC boundary this would have rolled the
    # day over in the middle of a morning session.
    morning = datetime(2026, 8, 29, 7, 0, tzinfo=STREAK_TZ)
    assert today_in_streak_tz(morning) == TODAY


def test_a_naive_timestamp_is_read_as_utc() -> None:
    assert today_in_streak_tz(datetime(2026, 8, 29, 12, 0)) == TODAY
