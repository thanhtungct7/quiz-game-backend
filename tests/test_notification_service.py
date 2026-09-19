from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from app.models.game.season import GameSeason
from app.models.notification.notification_dispatch import NotificationKind
from app.models.notification.user_device_token import DeviceType
from app.repository.notification.device_token_repository import QuestRecipient, Recipient
from app.services.notification.messages import ROUTE_LEADERBOARD, PushMessage
from app.services.notification.notification_service import NotificationService

TODAY = date(2026, 9, 14)
# 20:30 on the 14th in Vietnam.
EVENING = datetime(2026, 9, 14, 13, 30, tzinfo=UTC)
REMINDER_HOUR = 20


@dataclass
class Device:
    user_id: str
    token: str
    active: bool = True


class FakeDispatches:
    """`claim` as the unique constraint makes it: first writer wins."""

    def __init__(self) -> None:
        self.sent: set[tuple[str, str, str]] = set()
        # Users another process claims between the lookup and our claim.
        self.claimed_elsewhere: set[str] = set()

    async def claim(self, user_ids: list[str], kind: str, dedupe_key: str) -> set[str]:
        for user_id in self.claimed_elsewhere:
            self.sent.add((user_id, kind, dedupe_key))
        claimed = {u for u in user_ids if (u, kind, dedupe_key) not in self.sent}
        self.sent.update((u, kind, dedupe_key) for u in claimed)
        return claimed


class FakeTokens:
    """Mirrors DeviceTokenRepository's filters over in-memory rows."""

    def __init__(self, dispatches: FakeDispatches) -> None:
        self.dispatches = dispatches
        self.devices: list[Device] = []
        self.profiles: dict[str, tuple[int, date | None]] = {}
        self.upserts: list[tuple[str, str, DeviceType]] = []
        self.removals: list[tuple[str, str]] = []
        # user -> (points, quests left, chests opened) for the quest reminder.
        self.quest_days: dict[str, tuple[int, int, int]] = {}
        self.quest_query: dict[str, object] = {}

    def add(
        self, user_id: str, *tokens: str, streak: int = 0, last_active: date | None = None
    ) -> None:
        self.devices.extend(Device(user_id, token) for token in tokens)
        self.profiles[user_id] = (streak, last_active)

    def is_active(self, token: str) -> bool:
        return next(device.active for device in self.devices if device.token == token)

    async def upsert(self, user_id: str, fcm_token: str, device_type: DeviceType) -> None:
        self.upserts.append((user_id, fcm_token, device_type))

    async def remove(self, user_id: str, fcm_token: str) -> None:
        self.removals.append((user_id, fcm_token))

    async def deactivate(self, fcm_tokens: list[str]) -> None:
        for device in self.devices:
            if device.token in fcm_tokens:
                device.active = False

    async def pending_recipients(
        self,
        kind: str,
        dedupe_key: str,
        *,
        limit: int,
        not_active_on: date | None = None,
    ) -> list[Recipient]:
        recipients: list[Recipient] = []
        for user_id in sorted({device.user_id for device in self.devices if device.active}):
            if (user_id, kind, dedupe_key) in self.dispatches.sent:
                continue
            streak, last_active = self.profiles.get(user_id, (0, None))
            if not_active_on is not None and last_active is not None:
                if last_active >= not_active_on:
                    continue
            recipients.append(Recipient(user_id, streak, last_active))
        return recipients[:limit]

    async def pending_quest_recipients(
        self,
        kind: str,
        dedupe_key: str,
        day: date,
        *,
        milestones: list[int],
        limit: int,
    ) -> list[QuestRecipient]:
        self.quest_query = {"milestones": milestones}
        recipients: list[QuestRecipient] = []
        for user_id in sorted({device.user_id for device in self.devices if device.active}):
            if (user_id, kind, dedupe_key) in self.dispatches.sent:
                continue
            _, last_active = self.profiles.get(user_id, (0, None))
            if last_active != day or user_id not in self.quest_days:
                continue
            points, left, opened = self.quest_days[user_id]
            ready = sum(1 for milestone in milestones if points >= milestone) - opened
            if left > 0 or ready > 0:
                recipients.append(QuestRecipient(user_id, left, ready))
        return recipients[:limit]

    async def active_tokens_by_user(self, user_ids: list[str]) -> dict[str, list[str]]:
        tokens: dict[str, list[str]] = {}
        for device in self.devices:
            if device.active and device.user_id in user_ids:
                tokens.setdefault(device.user_id, []).append(device.token)
        return tokens


class FakeSender:
    def __init__(
        self, dead: Sequence[str] = (), failing_titles: Sequence[str] = ()
    ) -> None:
        self.dead = set(dead)
        self.failing_titles = set(failing_titles)
        self.sent: list[tuple[list[str], PushMessage]] = []

    async def send(self, tokens: Sequence[str], message: PushMessage) -> list[str]:
        if message.title in self.failing_titles:
            raise RuntimeError("FCM is down")
        self.sent.append((list(tokens), message))
        return [token for token in tokens if token in self.dead]

    def tokens_sent(self) -> list[str]:
        return sorted(token for tokens, _ in self.sent for token in tokens)


def _setup(
    sender: FakeSender | None = None,
) -> tuple[NotificationService, FakeTokens, FakeDispatches, FakeSender]:
    dispatches = FakeDispatches()
    tokens = FakeTokens(dispatches)
    sender = sender or FakeSender()
    service = NotificationService(
        tokens=tokens,  # type: ignore[arg-type]
        dispatches=dispatches,  # type: ignore[arg-type]
        sender=sender,
    )
    return service, tokens, dispatches, sender


async def test_no_reminder_goes_out_before_the_hour() -> None:
    service, tokens, _, sender = _setup()
    tokens.add("a", "a1")

    sent = await service.send_streak_reminders(
        datetime(2026, 9, 14, 12, 59, tzinfo=UTC), REMINDER_HOUR
    )

    assert sent == 0
    assert sender.sent == []


async def test_everyone_who_has_not_studied_today_is_reminded_once() -> None:
    service, tokens, _, sender = _setup()
    tokens.add("a", "a1", "a2", streak=4, last_active=TODAY - timedelta(days=1))
    tokens.add("b", "b1")
    tokens.add("c", "c1", streak=9, last_active=TODAY)

    first = await service.send_streak_reminders(EVENING, REMINDER_HOUR)
    again = await service.send_streak_reminders(EVENING + timedelta(minutes=1), REMINDER_HOUR)

    assert first == 2
    assert again == 0
    assert sender.tokens_sent() == ["a1", "a2", "b1"]
    at_risk = next(message for tokens_, message in sender.sent if "a1" in tokens_)
    assert "4 ngày" in at_risk.body


async def test_learners_with_the_same_wording_share_one_request() -> None:
    service, tokens, _, sender = _setup()
    tokens.add("b", "b1")
    tokens.add("d", "d1")

    await service.send_streak_reminders(EVENING, REMINDER_HOUR)

    assert len(sender.sent) == 1
    assert sorted(sender.sent[0][0]) == ["b1", "d1"]


async def test_a_learner_claimed_by_another_process_is_not_sent_to() -> None:
    service, tokens, dispatches, sender = _setup()
    tokens.add("a", "a1")
    tokens.add("b", "b1")
    dispatches.claimed_elsewhere = {"a"}

    sent = await service.send_streak_reminders(EVENING, REMINDER_HOUR)

    assert sent == 1
    assert sender.tokens_sent() == ["b1"]


async def test_tokens_fcm_calls_dead_are_switched_off() -> None:
    service, tokens, _, _ = _setup(FakeSender(dead=["a1"]))
    tokens.add("a", "a1", "a2")

    await service.send_streak_reminders(EVENING, REMINDER_HOUR)

    assert not tokens.is_active("a1")
    assert tokens.is_active("a2")


async def test_one_failed_request_does_not_stop_the_others() -> None:
    service, tokens, _, sender = _setup(FakeSender(failing_titles=["🔥 Giữ chuỗi học"]))
    tokens.add("a", "a1", streak=4, last_active=TODAY - timedelta(days=1))
    tokens.add("b", "b1")

    sent = await service.send_streak_reminders(EVENING, REMINDER_HOUR)

    assert sent == 2
    assert sender.tokens_sent() == ["b1"]


# 21:45 on the 14th in Vietnam.
QUEST_EVENING = datetime(2026, 9, 14, 14, 45, tzinfo=UTC)


async def test_no_quest_reminder_goes_out_before_half_past_nine() -> None:
    service, tokens, _, sender = _setup()
    tokens.add("a", "a1", last_active=TODAY)
    tokens.quest_days["a"] = (25, 3, 0)

    sent = await service.send_quest_reminders(
        datetime(2026, 9, 14, 14, 29, tzinfo=UTC), 21, 30
    )

    assert sent == 0
    assert sender.sent == []


async def test_learners_with_quests_or_chests_left_are_nudged_once() -> None:
    service, tokens, _, sender = _setup()
    tokens.add("halfway", "h1", last_active=TODAY)
    tokens.quest_days["halfway"] = (55, 2, 1)
    tokens.add("unopened", "u1", last_active=TODAY)
    tokens.quest_days["unopened"] = (100, 0, 1)
    tokens.add("done", "d1", last_active=TODAY)
    tokens.quest_days["done"] = (100, 0, 3)

    first = await service.send_quest_reminders(QUEST_EVENING, 21, 30)
    again = await service.send_quest_reminders(QUEST_EVENING, 21, 30)

    assert (first, again) == (2, 0)
    assert sender.tokens_sent() == ["h1", "u1"]
    bodies = {tokens_[0]: message.body for tokens_, message in sender.sent}
    assert "2 nhiệm vụ" in bodies["h1"]
    assert "2 rương" in bodies["u1"]
    assert tokens.quest_query == {"milestones": [30, 60, 100]}


async def test_the_quest_reminder_skips_learners_the_streak_reminder_is_for() -> None:
    """Who has not studied today gets the streak reminder instead, so nobody
    is pushed twice in one evening."""
    service, tokens, _, sender = _setup()
    tokens.add("idle", "i1", last_active=TODAY - timedelta(days=1))
    tokens.quest_days["idle"] = (0, 4, 0)

    sent = await service.send_quest_reminders(QUEST_EVENING, 21, 30)

    assert sent == 0


def _season(starts_at: datetime) -> GameSeason:
    return GameSeason(
        code="2026-09",
        name="Mùa 09/2026",
        starts_at=starts_at,
        ends_at=starts_at + timedelta(days=30),
        is_active=True,
    )


async def test_a_new_season_is_announced_to_every_device_once() -> None:
    service, tokens, dispatches, sender = _setup()
    tokens.add("a", "a1", last_active=TODAY)
    tokens.add("b", "b1")
    season = _season(datetime(2026, 9, 14, 2, 0, tzinfo=UTC))  # 09:00 local
    now = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)

    first = await service.announce_season(season, now)
    again = await service.announce_season(season, now + timedelta(minutes=1))

    assert (first, again) == (2, 0)
    assert sender.tokens_sent() == ["a1", "b1"]
    assert all(message.route == ROUTE_LEADERBOARD for _, message in sender.sent)
    assert ("a", NotificationKind.SEASON_STARTED, "2026-09") in dispatches.sent


async def test_an_old_season_or_none_at_all_announces_nothing() -> None:
    service, tokens, _, sender = _setup()
    tokens.add("a", "a1")
    season = _season(datetime(2026, 9, 10, 2, 0, tzinfo=UTC))
    now = datetime(2026, 9, 14, 3, 0, tzinfo=UTC)

    assert await service.announce_season(season, now) == 0
    assert await service.announce_season(None, now) == 0
    assert sender.sent == []


async def test_registering_and_unregistering_reach_the_repository() -> None:
    service, tokens, _, _ = _setup()

    await service.register_device("a", "tok", DeviceType.ANDROID)
    await service.unregister_device("a", "tok")

    assert tokens.upserts == [("a", "tok", DeviceType.ANDROID)]
    assert tokens.removals == [("a", "tok")]
