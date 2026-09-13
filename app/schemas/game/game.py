from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

from app.models.game.game_item import EquipmentSlot, ItemKind, ItemRarity
from app.models.game.skill import SkillEffect, SkillUnlockKind
from app.models.game.user_skill import LOADOUT_SLOTS
from app.services.game.season import RankTier


class EnergyRead(BaseModel):
    current: int
    maximum: int
    # None when the bar is full and nothing is accruing.
    next_regen_at: datetime | None


class GameProfileRead(BaseModel):
    """A player's standing in the game layer.

    `exp_to_next_level` is derived rather than stored so it can never disagree
    with `total_exp`.
    """

    user_id: str
    level: int
    total_exp: int
    exp_for_current_level: int
    exp_for_next_level: int
    exp_to_next_level: int
    gold: int
    class_code: str | None
    class_name: str | None
    energy: EnergyRead
    day_streak: int
    best_day_streak: int
    # The chốt chặn năng lực `level` is being held at, or None once experience
    # alone would not carry the player past it anyway. A client shows the
    # Benchmark Exam prompt exactly when this is not null.
    pending_benchmark_level: int | None


class GameClassRead(BaseModel):
    code: str
    name: str
    description: str
    max_hp: int
    damage_permille: int
    starting_mana: int
    defence: int
    is_current: bool


class SkillLockReason(StrEnum):
    """Why a skill cannot be unlocked yet. Absent means it can be."""

    WRONG_CLASS = "WRONG_CLASS"
    NEEDS_PARENT = "NEEDS_PARENT"
    NEEDS_LEVEL = "NEEDS_LEVEL"
    NEEDS_GOLD = "NEEDS_GOLD"
    NEEDS_UNIT = "NEEDS_UNIT"


class SkillNodeRead(BaseModel):
    """One node of the tree, with everything the client needs to draw it."""

    id: str
    code: str
    name: str
    description: str
    effect: SkillEffect
    class_code: str | None
    tier: int
    parent_code: str | None
    mana_cost: int
    magnitude: int
    duration_rounds: int
    unlock_kind: SkillUnlockKind
    unlock_level: int
    gold_price: int
    unlock_unit_id: str | None
    owned: bool
    unlockable: bool
    locked_reason: SkillLockReason | None
    equipped_slot: int | None


class SkillTreeRead(BaseModel):
    level: int
    gold: int
    class_code: str | None
    loadout_slots: int = LOADOUT_SLOTS
    skills: list[SkillNodeRead]


class ChooseClassRequest(BaseModel):
    class_code: str = Field(min_length=1, max_length=32)


class BenchmarkExamResultRequest(BaseModel):
    """A pass on the Benchmark Exam bound to one chốt chặn năng lực.

    `cap_level` must be one of `cefr.LEVEL_CAPS` and one the player's raw
    level has already reached -- `GameService.record_benchmark_pass` is what
    actually checks both, this is only the shape of the request.
    """

    cap_level: int = Field(gt=0)


class LoadoutRequest(BaseModel):
    # Fewer than three is allowed; the empty list clears the bar.
    skill_ids: list[str] = Field(default_factory=list, max_length=LOADOUT_SLOTS)


class LoadoutSlotRead(BaseModel):
    slot_index: int
    skill_id: str
    code: str
    name: str
    effect: SkillEffect
    mana_cost: int


class LoadoutRead(BaseModel):
    slots: list[LoadoutSlotRead]


class ItemRead(BaseModel):
    id: str
    code: str
    name: str
    kind: ItemKind
    slot: EquipmentSlot | None
    rarity: ItemRarity
    bonus_exp_permille: int
    bonus_gold_permille: int
    quantity: int
    equipped: bool


class InventoryRead(BaseModel):
    items: list[ItemRead]
    # Already capped by `loot.total_bonus`, so this is what the next match or
    # lesson will actually pay.
    bonus_exp_permille: int
    bonus_gold_permille: int
    # The skin currently on show, or None for the CEFR band's own colour.
    skin_code: str | None = None


class WearSkinRequest(BaseModel):
    # None takes the current one off rather than being a missing field.
    skin_code: str | None = None


class ShopItemRead(BaseModel):
    """One row on the shelf.

    No bonus fields: everything the shop sells is a zero-bonus cosmetic, so
    there is nothing to report beyond what it looks like and what it costs.
    """

    id: str
    code: str
    name: str
    kind: ItemKind
    rarity: ItemRarity
    gold_price: int
    owned: bool


class ShopRead(BaseModel):
    # The balance travels with the shelf so a purchase's response leaves the
    # client nothing stale to re-fetch.
    gold: int
    items: list[ShopItemRead]



class EquipmentRequest(BaseModel):
    weapon_id: str | None = None
    armor_id: str | None = None
    trinket_id: str | None = None


class SeasonRead(BaseModel):
    code: str
    name: str
    starts_at: datetime
    ends_at: datetime
    rating: int
    peak_rating: int
    tier: RankTier
    next_tier: RankTier | None
    rating_to_next_tier: int | None
    matches_played: int
    wins: int
    losses: int
    draws: int
