from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game.game_item import EquipmentSlot, GameItem, ItemKind
from app.models.game.user_item import LootGrant, UserEquipment, UserItem


class ItemRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def list_items(self) -> list[GameItem]:
        statement = (
            select(GameItem).where(GameItem.is_active.is_(True)).order_by(GameItem.code)
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def get_item(self, item_id: str) -> GameItem | None:
        return await self.db.get(GameItem, item_id)

    async def get_by_code(self, code: str) -> GameItem | None:
        statement = select(GameItem).where(GameItem.code == code)
        result = await self.db.execute(statement)
        return result.scalar_one_or_none()

    async def upsert_item(self, code: str, data: dict[str, object]) -> GameItem:
        existing = await self.get_by_code(code)
        if existing is None:
            existing = GameItem(code=code)
            self.db.add(existing)
        for field, value in data.items():
            setattr(existing, field, value)
        await self.db.commit()
        await self.db.refresh(existing)
        return existing

    async def owned(self, user_id: str) -> list[tuple[UserItem, GameItem]]:
        statement = (
            select(UserItem, GameItem)
            .join(GameItem, GameItem.id == UserItem.item_id)
            .where(UserItem.user_id == user_id)
            .order_by(GameItem.code)
        )
        result = await self.db.execute(statement)
        return [(owned, item) for owned, item in result.all()]

    async def add_to_inventory(self, user_id: str, item_id: str) -> None:
        """Give one copy, counting duplicates instead of adding another row."""
        statement = (
            pg_insert(UserItem)
            .values(user_id=user_id, item_id=item_id, quantity=1)
            .on_conflict_do_update(
                constraint="uq_user_items_user_id_item_id",
                set_={"quantity": UserItem.quantity + 1},
            )
        )
        await self.db.execute(statement)

    async def equipment(self, user_id: str) -> list[tuple[UserEquipment, GameItem]]:
        statement = (
            select(UserEquipment, GameItem)
            .join(GameItem, GameItem.id == UserEquipment.item_id)
            .where(UserEquipment.user_id == user_id)
        )
        result = await self.db.execute(statement)
        return [(worn, item) for worn, item in result.all()]

    async def replace_equipment(
        self, user_id: str, by_slot: dict[EquipmentSlot, str]
    ) -> None:
        """Set every slot at once, so a swap cannot trip the unique constraint."""
        await self.db.execute(
            delete(UserEquipment).where(UserEquipment.user_id == user_id)
        )
        self.db.add_all(
            [
                UserEquipment(user_id=user_id, slot=slot, item_id=item_id)
                for slot, item_id in by_slot.items()
            ]
        )
        await self.db.commit()

    async def equipment_pool(self) -> list[GameItem]:
        statement = select(GameItem).where(
            GameItem.is_active.is_(True), GameItem.kind == ItemKind.EQUIPMENT
        )
        result = await self.db.execute(statement)
        return list(result.scalars().all())

    async def claim_loot(self, user_id: str, ref_id: str, item_id: str | None) -> bool:
        """Record that this match has paid out, and say whether it was new.

        Same shape as the gold ledger: the unique (user_id, ref_id) is what
        makes settling a match twice unable to drop a second chest.
        """
        statement = (
            pg_insert(LootGrant)
            .values(user_id=user_id, ref_id=ref_id, item_id=item_id)
            .on_conflict_do_nothing(constraint="uq_loot_grants_user_id_ref_id")
            .returning(LootGrant.id)
        )
        result = await self.db.execute(statement)
        return result.scalar_one_or_none() is not None
