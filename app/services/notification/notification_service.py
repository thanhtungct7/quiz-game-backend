"""Push notifications: the devices that receive them and the scheduled sends.

Scheduled sends are driven from the housekeeping sweep once a minute. Each call
handles at most one batch of recipients and is safe to repeat: recipients are
claimed in `notification_dispatches` before anything is sent, so the next sweep
carries on where this one stopped and nobody gets the same occasion twice. The
price of claiming first is that a send which fails outright is not retried -- a
missed reminder is the better failure than a repeated one.
"""

import logging
from collections.abc import Callable, Sequence
from datetime import datetime
from typing import Protocol

from app.models.game.season import GameSeason
from app.models.notification.notification_dispatch import NotificationKind
from app.models.notification.user_device_token import DeviceType
from app.repository.notification.device_token_repository import (
    DeviceTokenRepository,
    QuestRecipient,
)
from app.repository.notification.notification_dispatch_repository import (
    NotificationDispatchRepository,
)
from app.services.game.daily_quests import CHESTS
from app.services.game.streak import today_in_streak_tz
from app.services.notification.messages import (
    PushMessage,
    is_quest_reminder_due,
    is_season_announcement_due,
    is_streak_reminder_due,
    quest_reminder,
    season_started,
    streak_reminder,
)
from app.services.notification.push_sender import PushSender

logger = logging.getLogger(__name__)

RECIPIENTS_PER_SWEEP = 500


class _HasUserId(Protocol):
    @property
    def user_id(self) -> str: ...



class NotificationService:
    def __init__(
        self,
        *,
        tokens: DeviceTokenRepository,
        dispatches: NotificationDispatchRepository,
        sender: PushSender,
    ) -> None:
        self.tokens = tokens
        self.dispatches = dispatches
        self.sender = sender

    async def register_device(
        self, user_id: str, fcm_token: str, device_type: DeviceType
    ) -> None:
        """Called on every app start and on every token rotation, so repeating it
        changes nothing."""
        await self.tokens.upsert(user_id, fcm_token, device_type)

    async def unregister_device(self, user_id: str, fcm_token: str) -> None:
        """Forget a device on sign-out. An unknown token is a no-op."""
        await self.tokens.remove(user_id, fcm_token)

    async def send_streak_reminders(self, now: datetime, reminder_hour: int) -> int:
        """Remind everyone who has not studied today, once, after `reminder_hour`."""
        if not is_streak_reminder_due(now, reminder_hour):
            return 0
        today = today_in_streak_tz(now)
        key = today.isoformat()
        recipients = await self.tokens.pending_recipients(
            NotificationKind.STREAK_REMINDER,
            key,
            limit=RECIPIENTS_PER_SWEEP,
            not_active_on=today,
        )
        return await self._deliver(
            recipients,
            NotificationKind.STREAK_REMINDER,
            key,
            lambda recipient: streak_reminder(
                recipient.day_streak, recipient.last_active_date, today
            ),
        )

    async def send_quest_reminders(
        self, now: datetime, reminder_hour: int, reminder_minute: int
    ) -> int:
        """Nudge everyone who studied today and still has quests or chests left,
        once, after the reminder time."""
        if not is_quest_reminder_due(now, reminder_hour, reminder_minute):
            return 0
        today = today_in_streak_tz(now)
        key = today.isoformat()
        recipients = await self.tokens.pending_quest_recipients(
            NotificationKind.QUEST_REMINDER,
            key,
            today,
            milestones=[chest.milestone for chest in CHESTS],
            limit=RECIPIENTS_PER_SWEEP,
        )

        def compose(recipient: QuestRecipient) -> PushMessage:
            return quest_reminder(recipient.quests_left, recipient.chests_ready)

        return await self._deliver(recipients, NotificationKind.QUEST_REMINDER, key, compose)

    async def announce_season(self, season: GameSeason | None, now: datetime) -> int:
        """Tell everyone a new season is open, during the first day it runs."""
        if season is None or not is_season_announcement_due(season.starts_at, now):
            return 0
        recipients = await self.tokens.pending_recipients(
            NotificationKind.SEASON_STARTED, season.code, limit=RECIPIENTS_PER_SWEEP
        )
        message = season_started(season.name)
        return await self._deliver(
            recipients, NotificationKind.SEASON_STARTED, season.code, lambda _: message
        )

    async def _deliver[R: _HasUserId](
        self,
        recipients: Sequence[R],
        kind: NotificationKind,
        dedupe_key: str,
        compose: Callable[[R], PushMessage],
    ) -> int:
        """Claim, then send. Returns how many users were claimed."""
        if not recipients:
            return 0
        claimed = await self.dispatches.claim(
            [recipient.user_id for recipient in recipients], kind, dedupe_key
        )
        tokens_by_user = await self.tokens.active_tokens_by_user(sorted(claimed))

        # One request per distinct wording rather than one per user.
        batches: dict[PushMessage, list[str]] = {}
        for recipient in recipients:
            if recipient.user_id in claimed:
                batches.setdefault(compose(recipient), []).extend(
                    tokens_by_user.get(recipient.user_id, [])
                )

        dead: list[str] = []
        for message, tokens in batches.items():
            if not tokens:
                continue
            try:
                dead.extend(await self.sender.send(tokens, message))
            except Exception:  # noqa: BLE001 -- one failed batch must not sink the rest
                logger.exception("Sending %s push to %d device(s) failed", kind, len(tokens))
        await self.tokens.deactivate(dead)
        return len(claimed)
