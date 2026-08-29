"""Turning a player's stored build into the read-only loadout a match uses."""

from app.models.game.user_skill import LOADOUT_SLOTS
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.game.user_skill_repository import UserSkillRepository
from app.services.duo.combat import MAX_HP, PERMILLE_ONE, streak_buff
from app.services.duo.loadout import EquippedSkill, PlayerLoadout, default_loadout
from app.services.game.leveling import level_for_exp
from app.services.game.loot import StatBonus, total_bonus


class LoadoutBuilder:
    def __init__(
        self,
        *,
        profiles: GameProfileRepository,
        catalog: CatalogRepository,
        skills: UserSkillRepository,
        items: ItemRepository,
    ) -> None:
        self.profiles = profiles
        self.catalog = catalog
        self.skills = skills
        self.items = items

    async def build(self, user_id: str) -> PlayerLoadout:
        """Everything a match needs to know about one player, resolved once.

        A player with no profile or no class still gets a playable loadout on
        baseline stats rather than being turned away, so the feature never
        locks an existing account out of a match.
        """
        profile = await self.profiles.get_by_user(user_id)
        if profile is None:
            return default_loadout(user_id)

        max_hp = MAX_HP
        damage_permille = PERMILLE_ONE
        starting_mana = 0
        if profile.class_code is not None:
            class_row = await self.catalog.get_class(profile.class_code)
            if class_row is not None and class_row.is_active:
                max_hp = class_row.max_hp
                damage_permille = class_row.damage_permille
                starting_mana = class_row.starting_mana

        equipped = [
            EquippedSkill(
                skill_id=skill.id,
                code=skill.code,
                name=skill.name,
                slot=slot.slot_index,
                effect=skill.effect,
                mana_cost=skill.mana_cost,
                magnitude=skill.magnitude,
                duration_rounds=skill.duration_rounds,
            )
            for slot, skill in await self.skills.loadout(user_id)
            # Defensive: a skill retired from the catalog stays in the loadout
            # table until the player edits it, and must not be usable.
            if skill.is_active and slot.slot_index < LOADOUT_SLOTS
        ]

        # Equipment and the daily streak are folded in here, so the engine only
        # ever reads three finished numbers and never has to know where they
        # came from.
        gear = total_bonus(
            [
                StatBonus(
                    max_hp=item.bonus_max_hp,
                    damage_permille=item.bonus_damage_permille,
                    starting_mana=item.bonus_starting_mana,
                )
                for _worn, item in await self.items.equipment(user_id)
                if item.is_active
            ]
        )
        buff = streak_buff(profile.day_streak)

        return PlayerLoadout(
            user_id=user_id,
            level=level_for_exp(profile.total_exp),
            class_code=profile.class_code,
            max_hp=max_hp + gear.max_hp + buff.bonus_max_hp,
            starting_mana=starting_mana + gear.starting_mana + buff.bonus_starting_mana,
            damage_permille=damage_permille + gear.damage_permille,
            day_streak=profile.day_streak,
            skills=tuple(equipped),
        )
