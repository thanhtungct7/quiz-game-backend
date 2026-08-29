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


class GameClassRead(BaseModel):
    code: str
    name: str
    description: str
    max_hp: int
    damage_permille: int
    starting_mana: int
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
    bonus_max_hp: int
    bonus_damage_permille: int
    bonus_starting_mana: int
    quantity: int
    equipped: bool


class InventoryRead(BaseModel):
    items: list[ItemRead]
    # Already capped by `loot.total_bonus`, so this is what a match will use.
    bonus_max_hp: int
    bonus_damage_permille: int
    bonus_starting_mana: int


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
