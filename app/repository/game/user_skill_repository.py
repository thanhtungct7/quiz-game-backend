from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.game.skill import Skill
from app.models.game.user_skill import UserSkill, UserSkillLoadout


class UserSkillRepository:
    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    async def owned_skill_ids(self, user_id: str) -> set[str]:
        statement = select(UserSkill.skill_id).where(UserSkill.user_id == user_id)
        result = await self.db.execute(statement)
        return set(result.scalars().all())

    async def grant(self, user_id: str, skill_id: str) -> UserSkill:
        record = UserSkill(user_id=user_id, skill_id=skill_id)
        self.db.add(record)
        await self.db.commit()
        await self.db.refresh(record)
        return record

    async def grant_many(self, user_id: str, skill_ids: list[str]) -> None:
        if not skill_ids:
            return
        self.db.add_all(
            [UserSkill(user_id=user_id, skill_id=skill_id) for skill_id in skill_ids]
        )
        await self.db.commit()

    async def loadout(self, user_id: str) -> list[tuple[UserSkillLoadout, Skill]]:
        """The equipped skills in slot order, joined to their catalog rows."""
        statement = (
            select(UserSkillLoadout, Skill)
            .join(Skill, Skill.id == UserSkillLoadout.skill_id)
            .where(UserSkillLoadout.user_id == user_id)
            .order_by(UserSkillLoadout.slot_index)
        )
        result = await self.db.execute(statement)
        return [(slot, skill) for slot, skill in result.all()]

    async def equipped_slot_by_skill_id(self, user_id: str) -> dict[str, int]:
        statement = select(UserSkillLoadout).where(UserSkillLoadout.user_id == user_id)
        result = await self.db.execute(statement)
        return {row.skill_id: row.slot_index for row in result.scalars().all()}

    async def replace_loadout(self, user_id: str, skill_ids: list[str]) -> None:
        """Set all three slots at once.

        Replacing wholesale rather than patching one slot keeps the unique
        (user_id, slot_index) constraint from tripping on a reorder, where a
        skill briefly occupies a slot another skill is still leaving.
        """
        await self.db.execute(
            delete(UserSkillLoadout).where(UserSkillLoadout.user_id == user_id)
        )
        self.db.add_all(
            [
                UserSkillLoadout(user_id=user_id, slot_index=index, skill_id=skill_id)
                for index, skill_id in enumerate(skill_ids)
            ]
        )
        await self.db.commit()

    async def clear_loadout(self, user_id: str) -> None:
        await self.db.execute(
            delete(UserSkillLoadout).where(UserSkillLoadout.user_id == user_id)
        )
        await self.db.commit()
