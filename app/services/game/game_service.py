"""The game layer's read and write side: profile, class, skill tree, loadout.

None of this runs during a match. The engine resolves a loadout once when the
match starts and then works entirely from memory, so everything here is free to
take its time.
"""

from datetime import UTC, datetime
from uuid import uuid4

from app.core.exceptions import (
    GameClassNotFoundError,
    InvalidEquipmentError,
    InvalidLoadoutError,
    NotEnoughGoldError,
    SkillAlreadyOwnedError,
    SkillLockedError,
    SkillNotFoundError,
)
from app.models.game.game_item import EquipmentSlot, ItemKind
from app.models.game.gold_transaction import GoldReason
from app.models.game.skill import Skill, SkillUnlockKind
from app.models.game.user_game_profile import UserGameProfile
from app.models.game.user_skill import LOADOUT_SLOTS
from app.repository.duo.duo_rating_repository import DuoRatingRepository
from app.repository.game.catalog_repository import CatalogRepository
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.gold_transaction_repository import GoldTransactionRepository
from app.repository.game.item_repository import ItemRepository
from app.repository.game.season_repository import SeasonRepository
from app.repository.game.user_skill_repository import UserSkillRepository
from app.repository.progress.user_progress_repository import UserProgressRepository
from app.schemas.game.game import (
    EnergyRead,
    GameClassRead,
    GameProfileRead,
    InventoryRead,
    ItemRead,
    LoadoutRead,
    LoadoutSlotRead,
    SeasonRead,
    SkillLockReason,
    SkillNodeRead,
    SkillTreeRead,
)
from app.services.game.energy import MAX_ENERGY, current_energy, next_regen_at
from app.services.game.leveling import exp_for_level, exp_to_next_level, level_for_exp
from app.services.game.loot import StatBonus, total_bonus
from app.services.game.season import TIER_FLOORS, tier_floor, tier_for_rating
from app.services.game.season_service import ensure_active_season, opening_rating
from app.services.game.starters import ensure_starters

# Picking a class the first time is free. Changing later has a price, so a
# build is a commitment rather than something re-rolled before every match.
CLASS_CHANGE_GOLD = 500


class GameService:
    def __init__(
        self,
        *,
        profiles: GameProfileRepository,
        catalog: CatalogRepository,
        skills: UserSkillRepository,
        ledger: GoldTransactionRepository,
        progress: UserProgressRepository,
        items: ItemRepository,
        seasons: SeasonRepository,
        ratings: DuoRatingRepository,
    ) -> None:
        self.profiles = profiles
        self.catalog = catalog
        self.skills = skills
        self.ledger = ledger
        self.progress = progress
        self.items = items
        self.seasons = seasons
        self.ratings = ratings

    # --- profile -------------------------------------------------------------

    async def get_profile(self, user_id: str) -> GameProfileRead:
        """The caller's profile, created on first read.

        A player who has never finished a match still has a profile to look at,
        which keeps the client from having to special-case an empty state.
        """
        profile = await self._ensure_profile(user_id)
        # Derived from total_exp rather than read from the column, so a stale
        # cached level can never be shown to the player.
        level = level_for_exp(profile.total_exp)
        class_row = (
            await self.catalog.get_class(profile.class_code)
            if profile.class_code is not None
            else None
        )
        return GameProfileRead(
            user_id=profile.user_id,
            level=level,
            total_exp=profile.total_exp,
            exp_for_current_level=exp_for_level(level),
            exp_for_next_level=exp_for_level(level + 1),
            exp_to_next_level=exp_to_next_level(profile.total_exp),
            gold=profile.gold,
            class_code=profile.class_code,
            class_name=class_row.name if class_row is not None else None,
            energy=self._energy_read(profile),
            day_streak=profile.day_streak,
            best_day_streak=profile.best_day_streak,
        )

    def _energy_read(self, profile: UserGameProfile) -> EnergyRead:
        """Energy as of right now, regenerated lazily rather than stored."""
        now = datetime.now(UTC)
        return EnergyRead(
            current=current_energy(profile.energy, profile.energy_updated_at, now),
            maximum=MAX_ENERGY,
            next_regen_at=next_regen_at(profile.energy, profile.energy_updated_at, now),
        )

    async def _ensure_profile(self, user_id: str) -> UserGameProfile:
        """Get or create the profile, making sure the starter skills are there."""
        profile = await self.profiles.get_or_create(user_id)
        await self._grant_starters(user_id)
        return profile

    async def _grant_starters(self, user_id: str) -> None:
        """Make sure the neutral starters are owned, and equipped if the bar is
        empty.

        Run on every profile read, not only when `_ensure_profile` creates the
        row. The profile is created by whatever the player happens to do first,
        and that is almost never a `/game` call: queueing a match takes energy
        (`energy_service`), finishing one pays out (`settlement`), finishing a
        lesson refills (`lesson_rewards`) -- and all three reach
        `profiles.get_or_create` on their own. Granting only on creation
        therefore meant the row already existed by the time anyone asked for
        their skills, and the starters were never handed out at all.
        """
        await ensure_starters(self.catalog, self.skills, user_id)

    # --- classes -------------------------------------------------------------

    async def list_classes(self, user_id: str) -> list[GameClassRead]:
        profile = await self._ensure_profile(user_id)
        return [
            GameClassRead(
                code=row.code,
                name=row.name,
                description=row.description,
                max_hp=row.max_hp,
                damage_permille=row.damage_permille,
                starting_mana=row.starting_mana,
                is_current=row.code == profile.class_code,
            )
            for row in await self.catalog.list_classes()
        ]

    async def choose_class(self, user_id: str, class_code: str) -> GameProfileRead:
        """Pick or change a class.

        Changing clears the loadout, because the skills in it may belong to the
        class being left behind. Unlocked skills are never taken away -- switch
        back and the tree is still there.
        """
        target = await self.catalog.get_class(class_code)
        if target is None or not target.is_active:
            raise GameClassNotFoundError(f"No class with code {class_code}")

        profile = await self._ensure_profile(user_id)
        if profile.class_code == class_code:
            return await self.get_profile(user_id)

        if profile.class_code is not None:
            if profile.gold < CLASS_CHANGE_GOLD:
                raise NotEnoughGoldError(
                    f"Changing class costs {CLASS_CHANGE_GOLD} gold"
                )
            await self._spend_gold(
                user_id,
                amount=CLASS_CHANGE_GOLD,
                reason=GoldReason.CLASS_CHANGE,
                # A fresh reference each time: unlike a match settle, this is a
                # deliberate repeatable action, not something to deduplicate.
                ref_id=str(uuid4()),
                balance=profile.gold,
            )
            await self.skills.clear_loadout(user_id)

        # `_spend_gold` already wrote the new balance onto this same row.
        await self.profiles.save(
            profile,
            {
                "class_code": class_code,
                "class_chosen_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
        )
        return await self.get_profile(user_id)

    # --- skills --------------------------------------------------------------

    async def get_skill_tree(self, user_id: str) -> SkillTreeRead:
        profile = await self._ensure_profile(user_id)
        level = level_for_exp(profile.total_exp)
        catalog = await self.catalog.list_skills()
        owned = await self.skills.owned_skill_ids(user_id)
        equipped = await self.skills.equipped_slot_by_skill_id(user_id)
        owned_codes = {skill.code for skill in catalog if skill.id in owned}
        completed_units = await self.progress.completed_unit_ids(user_id)

        nodes = []
        for skill in catalog:
            reason = self._lock_reason(
                skill,
                class_code=profile.class_code,
                level=level,
                gold=profile.gold,
                owned_codes=owned_codes,
                completed_units=completed_units,
            )
            is_owned = skill.id in owned
            nodes.append(
                SkillNodeRead(
                    id=skill.id,
                    code=skill.code,
                    name=skill.name,
                    description=skill.description,
                    effect=skill.effect,
                    class_code=skill.class_code,
                    tier=skill.tier,
                    parent_code=skill.parent_code,
                    mana_cost=skill.mana_cost,
                    magnitude=skill.magnitude,
                    duration_rounds=skill.duration_rounds,
                    unlock_kind=skill.unlock_kind,
                    unlock_level=skill.unlock_level,
                    gold_price=skill.gold_price,
                    unlock_unit_id=skill.unlock_unit_id,
                    owned=is_owned,
                    unlockable=not is_owned and reason is None,
                    locked_reason=None if is_owned else reason,
                    equipped_slot=equipped.get(skill.id),
                )
            )
        return SkillTreeRead(
            level=level, gold=profile.gold, class_code=profile.class_code, skills=nodes
        )

    def _lock_reason(
        self,
        skill: Skill,
        *,
        class_code: str | None,
        level: int,
        gold: int,
        owned_codes: set[str],
        completed_units: set[str],
    ) -> SkillLockReason | None:
        """The first condition this skill fails, or None if it is unlockable."""
        if skill.class_code is not None and skill.class_code != class_code:
            return SkillLockReason.WRONG_CLASS
        if skill.unlock_kind is SkillUnlockKind.UNIT_COMPLETION:
            if skill.unlock_unit_id is None or skill.unlock_unit_id not in completed_units:
                return SkillLockReason.NEEDS_UNIT
            return None
        if skill.parent_code is not None and skill.parent_code not in owned_codes:
            return SkillLockReason.NEEDS_PARENT
        if level < skill.unlock_level:
            return SkillLockReason.NEEDS_LEVEL
        if gold < skill.gold_price:
            return SkillLockReason.NEEDS_GOLD
        return None

    async def unlock_skill(self, user_id: str, skill_id: str) -> SkillNodeRead:
        skill = await self.catalog.get_skill(skill_id)
        if skill is None or not skill.is_active:
            raise SkillNotFoundError(f"No skill with id {skill_id}")

        profile = await self._ensure_profile(user_id)
        owned = await self.skills.owned_skill_ids(user_id)
        if skill_id in owned:
            raise SkillAlreadyOwnedError("You already own this skill")

        catalog = await self.catalog.list_skills()
        owned_codes = {row.code for row in catalog if row.id in owned}
        reason = self._lock_reason(
            skill,
            class_code=profile.class_code,
            level=level_for_exp(profile.total_exp),
            gold=profile.gold,
            owned_codes=owned_codes,
            completed_units=await self.progress.completed_unit_ids(user_id),
        )
        if reason is SkillLockReason.NEEDS_GOLD:
            raise NotEnoughGoldError(f"{skill.name} costs {skill.gold_price} gold")
        if reason is not None:
            raise SkillLockedError(f"{skill.name} is not available yet: {reason.value}")

        if skill.gold_price > 0:
            await self._spend_gold(
                user_id,
                amount=skill.gold_price,
                reason=GoldReason.SKILL_UNLOCK,
                # Keyed on the skill, so a retried purchase cannot charge twice.
                ref_id=skill.id,
                balance=profile.gold,
            )
        await self.skills.grant(user_id, skill_id)

        tree = await self.get_skill_tree(user_id)
        return next(node for node in tree.skills if node.id == skill_id)

    # --- loadout -------------------------------------------------------------

    async def get_loadout(self, user_id: str) -> LoadoutRead:
        await self._ensure_profile(user_id)
        return LoadoutRead(
            slots=[
                LoadoutSlotRead(
                    slot_index=slot.slot_index,
                    skill_id=skill.id,
                    code=skill.code,
                    name=skill.name,
                    effect=skill.effect,
                    mana_cost=skill.mana_cost,
                )
                for slot, skill in await self.skills.loadout(user_id)
            ]
        )

    async def set_loadout(self, user_id: str, skill_ids: list[str]) -> LoadoutRead:
        if len(skill_ids) > LOADOUT_SLOTS:
            raise InvalidLoadoutError(f"A loadout holds at most {LOADOUT_SLOTS} skills")
        if len(set(skill_ids)) != len(skill_ids):
            raise InvalidLoadoutError("The same skill cannot fill two slots")

        profile = await self._ensure_profile(user_id)
        owned = await self.skills.owned_skill_ids(user_id)
        missing = [skill_id for skill_id in skill_ids if skill_id not in owned]
        if missing:
            raise InvalidLoadoutError("You do not own every skill in that loadout")

        catalog = {skill.id: skill for skill in await self.catalog.list_skills()}
        for skill_id in skill_ids:
            skill = catalog.get(skill_id)
            if skill is None:
                raise SkillNotFoundError(f"No skill with id {skill_id}")
            # A skill unlocked under a previous class stays owned but cannot be
            # equipped while playing a different one.
            if skill.class_code is not None and skill.class_code != profile.class_code:
                raise InvalidLoadoutError(
                    f"{skill.name} belongs to another class"
                )

        await self.skills.replace_loadout(user_id, skill_ids)
        return await self.get_loadout(user_id)

    # --- gold ----------------------------------------------------------------

    async def _spend_gold(
        self, user_id: str, *, amount: int, reason: GoldReason, ref_id: str, balance: int
    ) -> None:
        if balance < amount:
            raise NotEnoughGoldError("Not enough gold")
        remaining = balance - amount
        granted = await self.ledger.grant(
            user_id=user_id,
            amount=-amount,
            reason=reason,
            ref_id=ref_id,
            balance_after=remaining,
        )
        if not granted:
            # A ledger row for this exact reference already exists, so this
            # purchase has already been paid for.
            raise SkillAlreadyOwnedError("That purchase has already been made")
        profile = await self.profiles.get_by_user(user_id)
        if profile is not None:
            await self.profiles.save(
                profile, {"gold": remaining, "updated_at": datetime.now(UTC)}
            )

    # --- inventory -----------------------------------------------------------

    async def get_inventory(self, user_id: str) -> InventoryRead:
        await self._ensure_profile(user_id)
        equipped_ids = {
            item.id for _worn, item in await self.items.equipment(user_id)
        }
        rows = [
            ItemRead(
                id=item.id,
                code=item.code,
                name=item.name,
                kind=item.kind,
                slot=item.slot,
                rarity=item.rarity,
                bonus_max_hp=item.bonus_max_hp,
                bonus_damage_permille=item.bonus_damage_permille,
                bonus_starting_mana=item.bonus_starting_mana,
                quantity=owned.quantity,
                equipped=item.id in equipped_ids,
            )
            for owned, item in await self.items.owned(user_id)
        ]
        # Report the capped total, so the client shows what a match will really
        # use rather than the raw sum of the labels.
        bonus = total_bonus(
            [
                StatBonus(
                    max_hp=row.bonus_max_hp,
                    damage_permille=row.bonus_damage_permille,
                    starting_mana=row.bonus_starting_mana,
                )
                for row in rows
                if row.equipped
            ]
        )
        return InventoryRead(
            items=rows,
            bonus_max_hp=bonus.max_hp,
            bonus_damage_permille=bonus.damage_permille,
            bonus_starting_mana=bonus.starting_mana,
        )

    async def set_equipment(
        self, user_id: str, requested: dict[EquipmentSlot, str | None]
    ) -> InventoryRead:
        await self._ensure_profile(user_id)
        owned = {item.id: item for _row, item in await self.items.owned(user_id)}

        by_slot: dict[EquipmentSlot, str] = {}
        for slot, item_id in requested.items():
            if item_id is None:
                continue
            item = owned.get(item_id)
            if item is None:
                raise InvalidEquipmentError("You do not own that item")
            if item.kind is not ItemKind.EQUIPMENT:
                raise InvalidEquipmentError(f"{item.name} is not equipment")
            if item.slot is not slot:
                raise InvalidEquipmentError(f"{item.name} does not go in that slot")
            by_slot[slot] = item_id

        await self.items.replace_equipment(user_id, by_slot)
        return await self.get_inventory(user_id)

    # --- season --------------------------------------------------------------

    async def get_current_season(self, user_id: str) -> SeasonRead:
        await self._ensure_profile(user_id)
        season = await ensure_active_season(self.seasons)
        record = await self.seasons.rating(season.id, user_id)
        if record is None:
            # Not played this season yet: show where they would start, without
            # writing a row for someone who may never queue.
            all_time = await self.ratings.get_by_user(user_id)
            rating = opening_rating(all_time.rating if all_time is not None else None)
            peak, played, wins, losses, draws = rating, 0, 0, 0, 0
        else:
            rating = record.rating
            peak = record.peak_rating
            played, wins = record.matches_played, record.wins
            losses, draws = record.losses, record.draws

        tier = tier_for_rating(rating)
        higher = [
            candidate for candidate, floor in TIER_FLOORS if floor > tier_floor(tier)
        ]
        next_tier = higher[-1] if higher else None
        return SeasonRead(
            code=season.code,
            name=season.name,
            starts_at=season.starts_at,
            ends_at=season.ends_at,
            rating=rating,
            peak_rating=peak,
            tier=tier,
            next_tier=next_tier,
            rating_to_next_tier=(
                tier_floor(next_tier) - rating if next_tier is not None else None
            ),
            matches_played=played,
            wins=wins,
            losses=losses,
            draws=draws,
        )
