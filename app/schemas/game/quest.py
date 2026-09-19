from datetime import date, datetime

from pydantic import BaseModel

from app.models.game.game_item import ItemRarity
from app.services.game.daily_quests import ChestTier, QuestDifficulty, QuestType


class DailyQuestRead(BaseModel):
    id: str
    code: str
    quest_type: QuestType
    difficulty: QuestDifficulty
    title: str
    progress: int
    target: int
    activity_points: int
    reward_gold: int
    reward_exp: int
    completed: bool
    claimed: bool


class ActivityChestRead(BaseModel):
    tier: ChestTier
    name: str
    milestone: int
    reward_gold: int
    reward_exp: int
    # The gold chest also rolls one item from the loot table.
    rolls_item: bool
    reached: bool
    claimed: bool


class DailyQuestsRead(BaseModel):
    """Today's quests and chests, and when they expire."""

    quest_date: date
    resets_at: datetime
    activity_points: int
    max_activity_points: int
    quests: list[DailyQuestRead]
    chests: list[ActivityChestRead]
    # Finished quests and reached chests not claimed yet: the red dot.
    claimable_count: int


class QuestExpChange(BaseModel):
    before: int
    after: int
    delta: int
    level_before: int
    level_after: int
    leveled_up: bool


class QuestGoldChange(BaseModel):
    before: int
    after: int
    delta: int


class QuestLootRead(BaseModel):
    code: str
    name: str
    rarity: ItemRarity


class QuestRewardRead(BaseModel):
    exp: QuestExpChange
    gold: QuestGoldChange
    loot: QuestLootRead | None = None


class QuestClaimRead(BaseModel):
    """What a claim paid, and the day as it stands afterwards."""

    reward: QuestRewardRead
    quests: DailyQuestsRead


class QuestCompletedRead(BaseModel):
    """A quest finished by the battle or match just played, for the result screen."""

    id: str
    title: str
    activity_points: int
