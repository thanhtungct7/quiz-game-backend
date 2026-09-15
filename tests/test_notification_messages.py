from datetime import UTC, date, datetime, timedelta

from app.services.notification.messages import (
    ROUTE_LEADERBOARD,
    ROUTE_LEARN,
    SEASON_CHANNEL,
    STREAK_CHANNEL,
    is_season_announcement_due,
    is_streak_reminder_due,
    season_started,
    streak_reminder,
)

TODAY = date(2026, 9, 14)


def _utc(hour: int, minute: int = 0, day: int = 14) -> datetime:
    return datetime(2026, 9, day, hour, minute, tzinfo=UTC)


def test_the_streak_reminder_waits_for_the_hour_in_the_learners_timezone() -> None:
    # 20:00 in Vietnam is 13:00 UTC.
    assert not is_streak_reminder_due(_utc(12, 59), reminder_hour=20)
    assert is_streak_reminder_due(_utc(13, 0), reminder_hour=20)
    assert is_streak_reminder_due(_utc(16, 59), reminder_hour=20)


def test_the_reminder_window_closes_at_local_midnight_not_utc() -> None:
    # 17:00 UTC is already 00:00 the next day in Vietnam.
    assert not is_streak_reminder_due(_utc(17, 0), reminder_hour=20)


def test_a_learner_who_studied_yesterday_is_told_the_streak_is_at_stake() -> None:
    message = streak_reminder(5, TODAY - timedelta(days=1), TODAY)

    assert "5 ngày" in message.body
    assert message.channel_id == STREAK_CHANNEL
    assert message.data == {"route": ROUTE_LEARN}


def test_a_streak_already_broken_gets_the_same_nudge_as_no_streak() -> None:
    broken = streak_reminder(5, TODAY - timedelta(days=3), TODAY)

    assert broken == streak_reminder(0, None, TODAY)
    assert "5" not in broken.body
    assert broken.data == {"route": ROUTE_LEARN}


def test_a_season_is_announced_in_the_daytime_of_its_first_day() -> None:
    started = _utc(20)  # 03:00 on the 15th in Vietnam

    assert not is_season_announcement_due(started, started + timedelta(minutes=1))
    assert is_season_announcement_due(started, _utc(2, day=15))  # 09:00 local
    assert not is_season_announcement_due(started, _utc(20, day=15))  # a full day later


def test_nothing_is_announced_before_the_season_starts() -> None:
    started = _utc(3, day=15)  # 10:00 local

    assert not is_season_announcement_due(started, _utc(2, day=15))


def test_the_season_announcement_opens_the_leaderboard() -> None:
    message = season_started("Mùa 10/2026")

    assert "Mùa 10/2026" in message.body
    assert message.channel_id == SEASON_CHANNEL
    assert message.data == {"route": ROUTE_LEADERBOARD}
