"""What a push says, and when a scheduled one is due.

Pure functions with no I/O; `now` and `today` are always parameters, the same
way `streak.py` takes them. Clock times are read in `STREAK_TZ`, so "20:00"
means 20:00 for the learner, not for the server.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from app.services.game.streak import STREAK_TZ

# Android notification channels. The app creates channels with these ids, so a
# learner can mute one kind of push without muting the other.
STREAK_CHANNEL = "streak"
SEASON_CHANNEL = "season"
QUEST_CHANNEL = "quest"

# Where a tap on the notification lands. The app maps these onto its own screens.
ROUTE_LEARN = "learn"
ROUTE_LEADERBOARD = "leaderboard"
ROUTE_QUESTS = "quests"

# A season can roll over at any hour; its announcement waits for the daytime.
DAYTIME_START_HOUR = 9
DAYTIME_END_HOUR = 21
# How long after a season opens its announcement is still news. A full day
# always contains a whole daytime span, whatever hour the rollover happened at.
SEASON_ANNOUNCEMENT_WINDOW = timedelta(days=1)


@dataclass(frozen=True)
class PushMessage:
    title: str
    body: str
    channel_id: str
    route: str

    @property
    def data(self) -> dict[str, str]:
        """The payload the app reads when the notification is tapped."""
        return {"route": self.route}


def local_hour(now: datetime) -> int:
    return now.astimezone(STREAK_TZ).hour


def is_streak_reminder_due(now: datetime, reminder_hour: int) -> bool:
    """From `reminder_hour` until the day ends. Who has already been reminded
    today is the dispatch record's business, not the clock's."""
    return local_hour(now) >= reminder_hour


def is_quest_reminder_due(now: datetime, reminder_hour: int, reminder_minute: int) -> bool:
    """From that local time until the day ends, like the streak reminder."""
    local = now.astimezone(STREAK_TZ)
    return (local.hour, local.minute) >= (reminder_hour, reminder_minute)


def is_season_announcement_due(season_started_at: datetime, now: datetime) -> bool:
    since_start = now - season_started_at
    return (
        timedelta(0) <= since_start < SEASON_ANNOUNCEMENT_WINDOW
        and DAYTIME_START_HOUR <= local_hour(now) < DAYTIME_END_HOUR
    )


def streak_reminder(day_streak: int, last_active_date: date | None, today: date) -> PushMessage:
    """A learner who studied yesterday is told what they stand to lose tonight.

    `day_streak` is only rewritten when the learner next studies, so a stale
    count from a streak already broken is not "at risk" -- that learner just
    gets the plain nudge.
    """
    if day_streak > 0 and last_active_date == today - timedelta(days=1):
        return PushMessage(
            title="🔥 Giữ chuỗi học",
            body=f"Đừng để mất chuỗi {day_streak} ngày! Vào làm ngay 1 bài học ngắn nhé!",
            channel_id=STREAK_CHANNEL,
            route=ROUTE_LEARN,
        )
    return PushMessage(
        title="📚 Hôm nay bạn chưa học",
        body="Vào làm 1 bài học ngắn để bắt đầu chuỗi ngày học nhé!",
        channel_id=STREAK_CHANNEL,
        route=ROUTE_LEARN,
    )


def season_started(season_name: str) -> PushMessage:
    return PushMessage(
        title="🏆 Mùa giải mới đã mở!",
        body=f"{season_name} đã bắt đầu. Tham gia tranh tài ngay.",
        channel_id=SEASON_CHANNEL,
        route=ROUTE_LEADERBOARD,
    )


def quest_reminder(quests_left: int, chests_ready: int) -> PushMessage:
    """An evening nudge for a learner who has played today.

    Chests already earned come first: they are a reward waiting, and they
    expire at midnight with the rest of the day's set.
    """
    if chests_ready > 0:
        return PushMessage(
            title="🎁 Rương ngày đang chờ bạn",
            body=f"Bạn còn {chests_ready} rương chưa mở. Vào nhận trước 0 giờ nhé!",
            channel_id=QUEST_CHANNEL,
            route=ROUTE_QUESTS,
        )
    return PushMessage(
        title="⚡ Rương Vàng đang chờ bạn!",
        body=f"Chỉ còn {quests_left} nhiệm vụ nữa để mở Rương Vàng hôm nay.",
        channel_id=QUEST_CHANNEL,
        route=ROUTE_QUESTS,
    )
