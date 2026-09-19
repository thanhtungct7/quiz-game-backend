"""The game layer's read and write side: profile, class, skill tree, loadout.

None of this runs during a match. The engine resolves a loadout once when the
match starts and then works entirely from memory, so everything here is free to
take its time.
"""

from datetime import UTC, datetime
from uuid import uuid4

from app.core.exceptions import (
    ApplicationError,
    BenchmarkExamNotEligibleError,
    GameClassNotFoundError,
    InvalidEquipmentError,
    InvalidLoadoutError,
    ItemAlreadyOwnedError,
    ItemNotForSaleError,
    ItemNotFoundError,
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
    ShopItemRead,
    ShopRead,
    SkillLockReason,
    SkillNodeRead,
    SkillTreeRead,
)
from app.services.game.cefr import LEVEL_CAPS
from app.services.game.combat_stats import attack_for
from app.services.game.energy import MAX_ENERGY, current_energy, next_regen_at
from app.services.game.leveling import (
    effective_level,
    exp_for_level,
    exp_to_next_level,
    pending_benchmark_level,
)
from app.services.game.loot import RewardBonus, total_bonus
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
        # cached level can never be shown to the player -- and held at the
        # player's Benchmark Exam cap, same as every other read of "the
        # current level".
        level = effective_level(profile.total_exp, profile.benchmark_cleared_level)
        pending_cap = pending_benchmark_level(profile.total_exp, profile.benchmark_cleared_level)
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
            # Capped at the current level while a Benchmark Exam is pending:
            # there is no next level to show progress toward until it is
            # passed, so both read as "nothing more to gain yet" rather than
            # racing ahead of a level number that cannot move.
            exp_for_next_level=(
                exp_for_level(level) if pending_cap is not None else exp_for_level(level + 1)
            ),
            exp_to_next_level=(
                0 if pending_cap is not None else exp_to_next_level(profile.total_exp)
            ),
            gold=profile.gold,
            class_code=profile.class_code,
            class_name=class_row.name if class_row is not None else None,
            skin_code=profile.skin_code,
            energy=self._energy_read(profile),
            day_streak=profile.day_streak,
            best_day_streak=profile.best_day_streak,
            pending_benchmark_level=pending_cap,
        )

    # --- benchmark exam --------------------------------------------------------

    async def clear_benchmark_cap(self, user_id: str, cap_level: int) -> GameProfileRead:
        """Clear one chốt chặn năng lực: the outcome of a graded Benchmark Exam
        sitting becomes a raised `benchmark_cleared_level`.

        Only `BenchmarkExamService` calls this, and only for a sitting it has
        graded as a pass -- there is no route that reaches it with a verdict
        from the client. Whether the cap could be sat at all was decided when
        the sitting opened, so it is not decided again here: experience that
        moved in the meantime must not take back a pass already earned.

        Anything not in `LEVEL_CAPS` is still refused, so a stray level number
        can never be recorded as cleared. Clearing the same cap twice, or a
        lower one than already held, is a no-op rather than an error:
        `benchmark_cleared_level` only ever rises.
        """
        if cap_level not in LEVEL_CAPS:
            raise BenchmarkExamNotEligibleError(f"{cap_level} is not a level cap")

        profile = await self._ensure_profile(user_id)
        if cap_level > profile.benchmark_cleared_level:
            await self.profiles.save(
                profile,
                {
                    "benchmark_cleared_level": cap_level,
                    "level": effective_level(profile.total_exp, cap_level),
                    "updated_at": datetime.now(UTC),
                },
            )
        return await self.get_profile(user_id)

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
                atk=attack_for(row.damage_permille),
                damage_permille=row.damage_permille,
                starting_mana=row.starting_mana,
                defence=row.defence,
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
        # Held at the Benchmark Exam cap: a skill gated on `unlock_level` past
        # a chốt chặn must not unlock on experience alone.
        level = effective_level(profile.total_exp, profile.benchmark_cleared_level)
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
            level=effective_level(profile.total_exp, profile.benchmark_cleared_level),
            gold=profile.gold,
            owned_codes=owned_codes,
            completed_units=await self.progress.completed_unit_ids(user_id),
        )
        if reason is SkillLockReason.NEEDS_GOLD:
            raise NotEnoughGoldError(f"{skill.name} costs {skill.gold_price} gold")
        if reason is not None:
            raise SkillLockedError(f"{skill.name} is not available yet: {reason.value}")

        # Staged before the charge, not after it. `_spend_gold` writes the new
        # balance last, so the skill and what it cost land in one commit;
        # granting afterwards left a window where a failure in between took the
        # gold and handed over nothing.
        await self.skills.grant(user_id, skill_id)
        if skill.gold_price > 0:
            await self._spend_gold(
                user_id,
                amount=skill.gold_price,
                reason=GoldReason.SKILL_UNLOCK,
                # Keyed on the skill, so a retried purchase cannot charge twice.
                ref_id=skill.id,
                balance=profile.gold,
            )
        else:
            # An ultimate is earned by finishing a unit rather than paid for, so
            # there is no balance write to carry the staged grant.
            await self.profiles.save(profile, {"updated_at": datetime.now(UTC)})

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
        self,
        user_id: str,
        *,
        amount: int,
        reason: GoldReason,
        ref_id: str,
        balance: int,
        already_paid: type[ApplicationError] = SkillAlreadyOwnedError,
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
            # purchase has already been paid for. What that means depends on
            # what was bought, so the caller names the error.
            raise already_paid("That purchase has already been made")
        profile = await self.profiles.get_by_user(user_id)
        if profile is not None:
            await self.profiles.save(
                profile, {"gold": remaining, "updated_at": datetime.now(UTC)}
            )

    # --- inventory -----------------------------------------------------------

    async def get_inventory(self, user_id: str) -> InventoryRead:
        profile = await self._ensure_profile(user_id)
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
                bonus_exp_permille=item.bonus_exp_permille,
                bonus_gold_permille=item.bonus_gold_permille,
                quantity=owned.quantity,
                equipped=item.id in equipped_ids,
            )
            for owned, item in await self.items.owned(user_id)
        ]
        # Report the capped total, so the client shows what a match or lesson
        # will really pay rather than the raw sum of the labels.
        bonus = total_bonus(
            [
                RewardBonus(
                    exp_permille=row.bonus_exp_permille,
                    gold_permille=row.bonus_gold_permille,
                )
                for row in rows
                if row.equipped
            ]
        )
        return InventoryRead(
            items=rows,
            bonus_exp_permille=bonus.exp_permille,
            bonus_gold_permille=bonus.gold_permille,
            skin_code=profile.skin_code,
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

    async def wear_skin(self, user_id: str, skin_code: str | None) -> InventoryRead:
        """Put one owned skin on show, or take the current one off with None.

        Deliberately not routed through `set_equipment`: a skin occupies no
        slot, competes with nothing, and moves no number -- it is the one thing
        a player wears that the engine never reads. Sharing the equipment path
        would mean widening the `equipment_slot` enum for something that has no
        slot to sit in.
        """
        profile = await self._ensure_profile(user_id)

        if skin_code is not None:
            owned = {
                item.code: item for _row, item in await self.items.owned(user_id)
            }
            item = owned.get(skin_code)
            if item is None:
                raise InvalidEquipmentError("You do not own that skin")
            if item.kind is not ItemKind.SKIN:
                raise InvalidEquipmentError(f"{item.name} is not a skin")

        await self.profiles.save(
            profile, {"skin_code": skin_code, "updated_at": datetime.now(UTC)}
        )
        return await self.get_inventory(user_id)

    # --- shop ----------------------------------------------------------------

    async def get_shop(self, user_id: str) -> ShopRead:
        profile = await self._ensure_profile(user_id)
        owned_ids = {item.id for _row, item in await self.items.owned(user_id)}
        return ShopRead(
            gold=profile.gold,
            items=[
                ShopItemRead(
                    id=item.id,
                    code=item.code,
                    name=item.name,
                    kind=item.kind,
                    rarity=item.rarity,
                    gold_price=item.gold_price,
                    owned=item.id in owned_ids,
                )
                for item in await self.items.list_purchasable()
            ],
        )

    async def purchase_item(self, user_id: str, item_id: str) -> ShopRead:
        """Buy one cosmetic outright, and hand back the whole refreshed shelf.

        Bought once and kept: `ref_id` is the item, so the ledger's unique
        (user, reason, ref) is itself the guard against a double tap charging
        twice. That is only sound because nothing on sale is consumable -- a
        second copy of a skin would be worth nothing anyway, which is why the
        ownership check below is a refusal rather than a top-up.

        Order matters. Every refusal is settled *before* the inventory row is
        staged, and `add_to_inventory` does not commit, so the item, the ledger
        row and the new balance all land in the single commit inside
        `_spend_gold`. Spending first and granting after -- the way
        `unlock_skill` does it -- leaves a window where a crash takes the gold
        without handing over the goods.
        """
        item = await self.items.get_item(item_id)
        if item is None or not item.is_active:
            raise ItemNotFoundError(f"No item with id {item_id}")
        if item.gold_price <= 0:
            raise ItemNotForSaleError(f"{item.name} is not for sale")

        profile = await self._ensure_profile(user_id)
        owned_ids = {owned.id for _row, owned in await self.items.owned(user_id)}
        if item_id in owned_ids:
            raise ItemAlreadyOwnedError(f"You already own {item.name}")
        if profile.gold < item.gold_price:
            raise NotEnoughGoldError(f"{item.name} costs {item.gold_price} gold")

        await self.items.add_to_inventory(user_id, item_id)
        await self._spend_gold(
            user_id,
            amount=item.gold_price,
            reason=GoldReason.ITEM_PURCHASE,
            ref_id=item_id,
            balance=profile.gold,
            already_paid=ItemAlreadyOwnedError,
        )
        return await self.get_shop(user_id)

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
