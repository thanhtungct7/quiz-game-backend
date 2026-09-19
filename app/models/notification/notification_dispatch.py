from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class NotificationKind(StrEnum):
    STREAK_REMINDER = "STREAK_REMINDER"
    SEASON_STARTED = "SEASON_STARTED"
    QUEST_REMINDER = "QUEST_REMINDER"


class NotificationDispatch(Base):
    """A scheduled push already sent to one user -- the record that keeps it to once.

    `dedupe_key` names the occasion within a kind: the local date for a streak
    or quest reminder, the season code for a season announcement. The unique constraint
    is what makes a send at-most-once even across a restart or a second process:
    the sender claims its recipients by inserting these rows first, and sends
    only to the rows it actually inserted.

    `kind` is a plain string rather than a database enum, so adding a kind needs
    no migration.
    """

    __tablename__ = "notification_dispatches"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "kind",
            "dedupe_key",
            name="uq_notification_dispatches_user_id_kind_dedupe_key",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    dedupe_key: Mapped[str] = mapped_column(String(64), nullable=False)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
