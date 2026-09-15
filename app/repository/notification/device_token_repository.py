from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import delete, exists, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.auth.user import User
from app.models.game.user_game_profile import UserGameProfile
from app.models.notification.notification_dispatch import NotificationDispatch
from app.models.notification.user_device_token import DeviceType, UserDeviceToken


@dataclass(frozen=True)
class Recipient:
    """A user a scheduled push is due for, with the standing its wording reads."""

    user_id: str
    day_streak: int
    last_active_date: date | None


class DeviceTokenRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def upsert(self, user_id: str, fcm_token: str, device_type: DeviceType) -> None:
        """Store a token for this user, taking it over if another account held it."""
        now = datetime.now(UTC)
        statement = (
            pg_insert(UserDeviceToken)
            .values(
                user_id=user_id,
                fcm_token=fcm_token,
                device_type=device_type,
                is_active=True,
                updated_at=now,
            )
            .on_conflict_do_update(
                constraint="uq_user_device_tokens_fcm_token",
                set_={
                    "user_id": user_id,
                    "device_type": device_type,
                    "is_active": True,
                    "updated_at": now,
                },
            )
        )
        await self.db.execute(statement)
        await self.db.commit()

    async def remove(self, user_id: str, fcm_token: str) -> None:
        """Only the caller's own token: a token another account now holds stays put."""
        statement = delete(UserDeviceToken).where(
            UserDeviceToken.user_id == user_id,
            UserDeviceToken.fcm_token == fcm_token,
        )
        await self.db.execute(statement)
        await self.db.commit()

    async def deactivate(self, fcm_tokens: list[str]) -> None:
        if not fcm_tokens:
            return
        statement = (
            update(UserDeviceToken)
            .where(UserDeviceToken.fcm_token.in_(fcm_tokens))
            .values(is_active=False, updated_at=datetime.now(UTC))
        )
        await self.db.execute(statement)
        await self.db.commit()

    async def pending_recipients(
        self,
        kind: str,
        dedupe_key: str,
        *,
        limit: int,
        not_active_on: date | None = None,
    ) -> list[Recipient]:
        """Users with a live device who have not been sent this occasion yet.

        `not_active_on` narrows it to users who have not studied on that day. A
        user with no game profile has never studied at all, so is included.
        """
        already_sent = exists().where(
            NotificationDispatch.user_id == User.id,
            NotificationDispatch.kind == kind,
            NotificationDispatch.dedupe_key == dedupe_key,
        )
        has_live_device = exists().where(
            UserDeviceToken.user_id == User.id,
            UserDeviceToken.is_active.is_(True),
        )
        statement = (
            select(User.id, UserGameProfile.day_streak, UserGameProfile.last_active_date)
            .select_from(User)
            .outerjoin(UserGameProfile, UserGameProfile.user_id == User.id)
            .where(User.is_active.is_(True), has_live_device, ~already_sent)
            .order_by(User.id)
            .limit(limit)
        )
        if not_active_on is not None:
            statement = statement.where(
                or_(
                    UserGameProfile.last_active_date.is_(None),
                    UserGameProfile.last_active_date < not_active_on,
                )
            )
        result = await self.db.execute(statement)
        return [
            Recipient(user_id=user_id, day_streak=streak or 0, last_active_date=last_active)
            for user_id, streak, last_active in result.all()
        ]

    async def active_tokens_by_user(self, user_ids: list[str]) -> dict[str, list[str]]:
        if not user_ids:
            return {}
        statement = select(UserDeviceToken.user_id, UserDeviceToken.fcm_token).where(
            UserDeviceToken.user_id.in_(user_ids),
            UserDeviceToken.is_active.is_(True),
        )
        result = await self.db.execute(statement)
        tokens: dict[str, list[str]] = {}
        for user_id, fcm_token in result.all():
            tokens.setdefault(user_id, []).append(fcm_token)
        return tokens
