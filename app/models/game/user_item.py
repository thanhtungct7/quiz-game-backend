from datetime import datetime
from uuid import uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.models.game.game_item import EquipmentSlot


class UserItem(Base):
    """One row per item a player owns, with duplicates counted rather than stacked."""

    __tablename__ = "user_items"
    __table_args__ = (
        UniqueConstraint("user_id", "item_id", name="uq_user_items_user_id_item_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("game_items.id", ondelete="CASCADE"), nullable=False, index=True
    )
    quantity: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    acquired_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class UserEquipment(Base):
    """What a player has equipped, one item per slot."""

    __tablename__ = "user_equipment"
    __table_args__ = (
        UniqueConstraint("user_id", "slot", name="uq_user_equipment_user_id_slot"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    slot: Mapped[EquipmentSlot] = mapped_column(
        Enum(EquipmentSlot, name="equipment_slot"), nullable=False
    )
    item_id: Mapped[str] = mapped_column(
        ForeignKey("game_items.id", ondelete="CASCADE"), nullable=False, index=True
    )


class LootGrant(Base):
    """Proof that a match has already handed out its chest.

    The unique (user_id, ref_id) is the guard: settling the same match twice
    cannot insert a second row, so it cannot drop a second item.
    """

    __tablename__ = "loot_grants"
    __table_args__ = (
        UniqueConstraint("user_id", "ref_id", name="uq_loot_grants_user_id_ref_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ref_id: Mapped[str] = mapped_column(String(36), nullable=False)
    item_id: Mapped[str | None] = mapped_column(
        ForeignKey("game_items.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
