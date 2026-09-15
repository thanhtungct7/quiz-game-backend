from uuid import uuid4

from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.notification.notification_dispatch import NotificationDispatch


class NotificationDispatchRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def claim(self, user_ids: list[str], kind: str, dedupe_key: str) -> set[str]:
        """Record this occasion as sent to each user, and return the users it
        was newly recorded for.

        A user someone else already claimed -- an earlier sweep, another
        process -- is left out of the result, which is what makes the send that
        follows at-most-once.
        """
        if not user_ids:
            return set()
        statement = (
            pg_insert(NotificationDispatch)
            .values(
                [
                    {
                        "id": str(uuid4()),
                        "user_id": user_id,
                        "kind": kind,
                        "dedupe_key": dedupe_key,
                    }
                    for user_id in user_ids
                ]
            )
            .on_conflict_do_nothing(
                constraint="uq_notification_dispatches_user_id_kind_dedupe_key"
            )
            .returning(NotificationDispatch.user_id)
        )
        result = await self.db.execute(statement)
        claimed = set(result.scalars().all())
        await self.db.commit()
        return claimed
