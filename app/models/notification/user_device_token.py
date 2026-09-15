from datetime import datetime
from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DeviceType(StrEnum):
    ANDROID = "ANDROID"
    IOS = "IOS"


class UserDeviceToken(Base):
    """One app install that can receive push notifications for a user.

    `fcm_token` is unique across users, not per user: a token names an install,
    so when a second account signs in on the same phone the token moves to that
    account instead of notifying both.

    `is_active` goes false when FCM reports the token dead (app uninstalled,
    token rotated). The row is kept rather than deleted, so registering the same
    token again simply revives it.
    """

    __tablename__ = "user_device_tokens"
    __table_args__ = (UniqueConstraint("fcm_token", name="uq_user_device_tokens_fcm_token"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fcm_token: Mapped[str] = mapped_column(Text, nullable=False)
    device_type: Mapped[DeviceType] = mapped_column(
        Enum(DeviceType, name="device_type"),
        nullable=False,
        default=DeviceType.ANDROID,
        server_default=DeviceType.ANDROID.value,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
