"""Turning a player's stored build into the read-only loadout a match uses."""

from app.models.game.user_skill import LOADOUT_SLOTS
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.game.user_skill_repository import UserSkillRepository
from app.services.game.combat_stats import ClassPart, class_part, item_part, resolve
from app.services.game.leveling import level_for_exp
from app.services.game.loadout import EquippedSkill, PlayerLoadout
from app.services.game.starters import ensure_starters


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

        Note what is deliberately *not* here: an early return for a player with
        no profile row. A brand-new account whose very first action is a battle
        has no profile yet, and returning `default_loadout` straight away sent
        them in with an empty skill bar -- the same broken promise the starter
        grant exists to keep, one layer further down. Everything below reads
        cleanly with `profile` absent, so the grant is reached either way.
        """
        profile = await self.profiles.get_by_user(user_id)

        base: ClassPart | None = None
        if profile is not None and profile.class_code is not None:
            class_row = await self.catalog.get_class(profile.class_code)
            if class_row is not None and class_row.is_active:
                base = class_part(class_row)

        rows = await self.skills.loadout(user_id)
        if not rows:
            # Nobody walks into a match with an empty bar -- and this is the
            # last point before the fight where that can still be made true.
            # A player who queued before ever opening a game screen reaches the
            # engine without having touched `GameService`, so the grant cannot
            # live there alone. Guarded on the bar being empty, so it costs a
            # single extra query for exactly the players who need it, and
            # nothing at all for everyone else.
            if await ensure_starters(self.catalog, self.skills, user_id):
                rows = await self.skills.loadout(user_id)

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
            for slot, skill in rows
            # Defensive: a skill retired from the catalog stays in the loadout
            # table until the player edits it, and must not be usable.
            if skill.is_active and slot.slot_index < LOADOUT_SLOTS
        ]

        # Class, equipment and the daily streak are added up by
        # `combat_stats.resolve`, which the profile screen also calls. Sharing
        # the arithmetic rather than repeating it is what makes the numbers on
        # a player's card the numbers they actually fight on.
        day_streak = profile.day_streak if profile is not None else 0
        build = resolve(
            base=base,
            equipment=[
                item_part(item)
                for _worn, item in await self.items.equipment(user_id)
                if item.is_active
            ],
            day_streak=day_streak,
        )

        return PlayerLoadout(
            user_id=user_id,
            level=level_for_exp(profile.total_exp) if profile is not None else 1,
            class_code=profile.class_code if profile is not None else None,
            max_hp=build.max_hp,
            starting_mana=build.starting_mana,
            damage_permille=build.damage_permille,
            defence=build.defence,
            day_streak=day_streak,
            skills=tuple(equipped),
        )

