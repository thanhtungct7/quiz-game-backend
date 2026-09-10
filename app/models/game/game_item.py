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

    Deliberately not a stat block any more. An EQUIPMENT item used to move the
    same four numbers a class moves -- HP, damage, mana, defence -- which is
    exactly the "cày đồ" pay-to-win loop the EdTech redesign removes: gear
    must flavour a collection, never buy a match. What it moves now is two
    EdTech buffs, `bonus_exp_permille` and `bonus_gold_permille`, applied to a
    match or lesson's payout in `settlement.GameSettlementService`, never to
    `combat.resolve_blow`. SKIN and CARD items are cosmetic and collectible
    and carry no bonus at all, which keeps the reward table interesting
    without letting it decide a payout either.
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
    # Thousandths, same convention as `damage_permille` used to be: 150 means
    # +15% on top of whatever a match or lesson would otherwise have paid.
    bonus_exp_permille: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    bonus_gold_permille: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    image_src: Mapped[str | None] = mapped_column(String(255), nullable=True)

    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="true"
    )
