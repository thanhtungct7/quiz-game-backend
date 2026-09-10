from enum import StrEnum
from uuid import uuid4

from sqlalchemy import Boolean, Enum, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class ItemKind(StrEnum):
    EQUIPMENT = "EQUIPMENT"
    SKIN = "SKIN"
    CARD = "CARD"


class EquipmentSlot(StrEnum):
    WEAPON = "WEAPON"
    ARMOR = "ARMOR"
    TRINKET = "TRINKET"


class ItemRarity(StrEnum):
    COMMON = "COMMON"
    RARE = "RARE"
    EPIC = "EPIC"
    LEGENDARY = "LEGENDARY"


class GameItem(Base):
    """Something a chest can drop.

    An EQUIPMENT item moves exactly the four numbers a class moves. SKIN and
    CARD items are cosmetic and collectible and carry no bonus at all, which
    keeps the reward table interesting without letting it decide fights.
    """

    __tablename__ = "game_items"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    code: Mapped[str] = mapped_column(String(32), nullable=False, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    kind: Mapped[ItemKind] = mapped_column(Enum(ItemKind, name="item_kind"), nullable=False)
    # NULL for anything that is not equipment.
    slot: Mapped[EquipmentSlot | None] = mapped_column(
        Enum(EquipmentSlot, name="equipment_slot"), nullable=True
    )
    rarity: Mapped[ItemRarity] = mapped_column(
        Enum(ItemRarity, name="item_rarity"), nullable=False
    )
    bonus_max_hp: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    bonus_damage_permille: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    bonus_starting_mana: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    # Flat damage taken off each incoming blow. Only armour carries it, so the
    # three equipment slots stay three different decisions.
    bonus_defence: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    image_src: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
