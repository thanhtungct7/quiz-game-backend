"""Daily quests: drawing a player's set, moving it forward, and paying it out.

Two classes, because the two halves are called from very different places.

`DailyQuestTracker` only needs the quest tables. It runs at the end of a lesson
battle, a duo match, a lesson and an AI conversation, inside whatever session
that code already opened, and it must never be the reason a payout fails --
see `track_quietly`.

`DailyQuestService` is the API's side: today's view and the two claims. A claim
writes the quest (or chest) row, the gold ledger and the profile in one
transaction; the claim row is the double-claim guard and the ledger's unique
(user, reason, ref_id) sits behind it as a second one.
"""

import logging
import random
from dataclasses import dataclass
from datetime import datetime

from app.core.exceptions import (
    ActivityChestAlreadyClaimedError,
    ActivityChestLockedError,
    ActivityChestNotFoundError,
    DailyQuestAlreadyClaimedError,
    DailyQuestExpiredError,
    DailyQuestNotCompletedError,
    DailyQuestNotFoundError,
)
from app.models.game.gold_transaction import GoldReason
from app.repository.game.daily_quest_repository import DailyQuestRepository, QuestRow
from app.repository.game.game_profile_repository import GameProfileRepository
from app.repository.game.gold_transaction_repository import GoldTransactionRepository
from app.repository.game.item_repository import ItemRepository
from app.schemas.game.quest import (
    ActivityChestRead,
    DailyQuestRead,
    DailyQuestsRead,
    QuestClaimRead,
    QuestExpChange,
    QuestGoldChange,
    QuestLootRead,
    QuestRewardRead,
)
from app.services.game.daily_quests import (
    CHESTS,
    MAX_ACTIVITY_POINTS,
    PEAK_TYPES,
    QuestEvent,
    activity_points,
    chest_for_milestone,
    draw_seed,
    next_reset_at,
    pick_daily_quests,
    quest_day,
)
from app.services.game.leveling import apply_exp, effective_level
from app.services.game.loot import ItemDrop, roll_item
from app.services.game.quest_catalog import quest_title

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletedQuest:
    """A quest some activity just finished, as the result screen names it."""

    id: str
    title: str
    activity_points: int


def _completed(row: QuestRow) -> CompletedQuest:
    quest, template = row
    return CompletedQuest(
        id=quest.id,
        title=quest_title(template.title, quest.target),
        activity_points=quest.activity_points,
    )


class DailyQuestTracker:
    def __init__(self, quests: DailyQuestRepository) -> None:
        self.quests = quests

    async def today(self, user_id: str, now: datetime) -> list[QuestRow]:
        """Today's set, drawing it first if this is the day's first look."""
        day = quest_day(now)
        rows = await self.quests.quests_for_day(user_id, day)
        if rows:
            return rows
        templates = await self.quests.active_templates()
        # Seeded by (user, day), so a request racing this one draws the same
        # four and the unique constraint drops the duplicates.
        drawn = pick_daily_quests(templates, random.Random(draw_seed(user_id, day)))  # noqa: S311
        await self.quests.insert_quests(
            [
                {
                    "user_id": user_id,
                    "template_id": template.id,
                    "quest_date": day,
                    "quest_type": template.quest_type,
                    "difficulty": template.difficulty,
                    "target": template.target,
                    "activity_points": template.activity_points,
                    "reward_gold": template.reward_gold,
                    "reward_exp": template.reward_exp,
                    "slot": slot,
                    "progress": 0,
                    "created_at": now,
                }
                for slot, template in enumerate(drawn)
            ]
        )
        return await self.quests.quests_for_day(user_id, day)

    async def track(self, user_id: str, event: QuestEvent, now: datetime) -> list[CompletedQuest]:
        """Count one finished activity toward today's quests. Returns the quests
        it finished.

        Draws today's set first when there is none yet, so the first match of
        the day counts even if the quest screen has not been opened.
        """
        touched = event.touched()
        if not touched:
            return []
        rows = await self.today(user_id, now)
        drawn_types = {quest.quest_type for quest, _ in rows}
        day = quest_day(now)
        finished: set[str] = set()
        for quest_type, amount in touched.items():
            if quest_type not in drawn_types:
                continue
            finished.update(
                await self.quests.advance(
                    user_id, day, quest_type, amount, now, peak=quest_type in PEAK_TYPES
                )
            )
        if not finished:
            return []
        return [
            _completed(row)
            for row in await self.quests.quests_for_day(user_id, day)
            if row[0].id in finished
        ]

    async def track_quietly(
        self, user_id: str, event: QuestEvent, now: datetime
    ) -> list[CompletedQuest]:
        """`track`, for callers in the middle of paying out something else.

        A quest is a bonus on top of a battle or a match. Losing one count to a
        database hiccup is the better failure than failing the payout it rode
        in on, so this logs and carries on.
        """
        try:
            return await self.track(user_id, event, now)
        except Exception:  # noqa: BLE001 -- see above
            logger.exception("Could not count activity toward %s's daily quests", user_id)
            # The session is the caller's; leave it usable for what it does next.
            await self.quests.rollback()
            return []

    async def completed_since(self, user_id: str, since: datetime) -> list[CompletedQuest]:
        """Every quest finished from `since` on -- "during this battle"."""
        return [_completed(row) for row in await self.quests.completed_since(user_id, since)]


class DailyQuestService:
    def __init__(
        self,
        *,
        quests: DailyQuestRepository,
        profiles: GameProfileRepository,
        ledger: GoldTransactionRepository,
        items: ItemRepository,
        rng: random.Random | None = None,
    ) -> None:
        self.quests = quests
        self.tracker = DailyQuestTracker(quests)
        self.profiles = profiles
        self.ledger = ledger
        self.items = items
        self.rng = rng or random.Random()  # noqa: S311 -- loot rolls, not security

    async def get_today(self, user_id: str, now: datetime) -> DailyQuestsRead:
        rows = await self.tracker.today(user_id, now)
        opened = await self.quests.claimed_chests(user_id, quest_day(now))
        return build_view(rows, opened, now)

    async def claim_quest(self, user_id: str, quest_id: str, now: datetime) -> QuestClaimRead:
        day = quest_day(now)
        quest = await self.quests.get_quest(user_id, quest_id)
        if quest is None:
            raise DailyQuestNotFoundError("Daily quest not found")
        if quest.quest_date != day:
            raise DailyQuestExpiredError("That quest belonged to an earlier day")
        if quest.claimed_at is not None:
            raise DailyQuestAlreadyClaimedError("That quest's reward was already claimed")
        if quest.completed_at is None:
            raise DailyQuestNotCompletedError("Finish the quest before claiming its reward")
        # The checks above give the right error; this is what stops a second
        # request that passed them at the same moment.
        if not await self.quests.mark_claimed(user_id, quest_id, day, now):
            raise DailyQuestAlreadyClaimedError("That quest's reward was already claimed")
        reward = await self._pay(
            user_id,
            now,
            gold=quest.reward_gold,
            exp=quest.reward_exp,
            reason=GoldReason.DAILY_QUEST,
            ref_id=quest.id,
        )
        return QuestClaimRead(reward=reward, quests=await self.get_today(user_id, now))

    async def claim_chest(self, user_id: str, milestone: int, now: datetime) -> QuestClaimRead:
        chest = chest_for_milestone(milestone)
        if chest is None:
            raise ActivityChestNotFoundError(f"No chest sits at {milestone} activity points")
        rows = await self.tracker.today(user_id, now)
        if _points(rows) < chest.milestone:
            raise ActivityChestLockedError(
                f"{chest.name} opens at {chest.milestone} activity points"
            )
        chest_id = await self.quests.open_chest(user_id, quest_day(now), chest.milestone, now)
        if chest_id is None:
            raise ActivityChestAlreadyClaimedError(f"{chest.name} was already opened today")
        reward = await self._pay(
            user_id,
            now,
            gold=chest.reward_gold,
            exp=chest.reward_exp,
            reason=GoldReason.ACTIVITY_CHEST,
            ref_id=chest_id,
            roll_item=chest.rolls_item,
        )
        return QuestClaimRead(reward=reward, quests=await self.get_today(user_id, now))

    async def _pay(
        self,
        user_id: str,
        now: datetime,
        *,
        gold: int,
        exp: int,
        reason: GoldReason,
        ref_id: str,
        roll_item: bool = False,
    ) -> QuestRewardRead:
        """Move gold, experience and (maybe) an item. Commits the claim with it.

        No equipment bonus: gear multiplies what a battle or a match pays, and
        a quest is a reward for having played those, not a second one of them.
        """
        profile = await self.profiles.get_or_create(user_id)
        gold_before, exp_before, level_before = profile.gold, profile.total_exp, profile.level
        gold_after = gold_before + gold
        granted = await self.ledger.grant(
            user_id=user_id,
            amount=gold,
            reason=reason,
            ref_id=ref_id,
            balance_after=gold_after,
        )
        if not granted:
            # Unreachable while the claim row guards first; kept so a ledger
            # that already holds this payout can never be paid a second time.
            raise DailyQuestAlreadyClaimedError("That reward was already paid")

        loot = await self._roll_item(user_id, ref_id) if roll_item else None

        exp_after = apply_exp(exp_before, exp, floor_at_current_level=True)
        level_after = effective_level(exp_after, profile.benchmark_cleared_level)
        # The one commit: the claim, the ledger row, the item and the balance
        # land together or not at all.
        await self.profiles.save(
            profile,
            {
                "total_exp": exp_after,
                "level": level_after,
                "gold": gold_after,
                "updated_at": now,
            },
        )
        return QuestRewardRead(
            exp=QuestExpChange(
                before=exp_before,
                after=exp_after,
                delta=exp_after - exp_before,
                level_before=level_before,
                level_after=level_after,
                leveled_up=level_after > level_before,
            ),
            gold=QuestGoldChange(before=gold_before, after=gold_after, delta=gold),
            loot=loot,
        )

    async def _roll_item(self, user_id: str, ref_id: str) -> QuestLootRead | None:
        """The gold chest's item: the same roll a won boss fight makes."""
        pool = [
            ItemDrop(item_id=item.id, code=item.code, name=item.name, rarity=item.rarity)
            for item in await self.items.drop_pool()
        ]
        drop = roll_item(self.rng, pool, won=True)
        claimed = await self.items.claim_loot(
            user_id, ref_id, drop.item_id if drop is not None else None
        )
        if not claimed or drop is None:
            return None
        await self.items.add_to_inventory(user_id, drop.item_id)
        return QuestLootRead(code=drop.code, name=drop.name, rarity=drop.rarity)


def _points(rows: list[QuestRow]) -> int:
    return activity_points([quest.activity_points for quest, _ in rows if quest.completed_at])


def build_view(rows: list[QuestRow], opened: set[int], now: datetime) -> DailyQuestsRead:
    quests = [
        DailyQuestRead(
            id=quest.id,
            code=template.code,
            quest_type=quest.quest_type,
            difficulty=quest.difficulty,
            title=quest_title(template.title, quest.target),
            progress=quest.progress,
            target=quest.target,
            activity_points=quest.activity_points,
            reward_gold=quest.reward_gold,
            reward_exp=quest.reward_exp,
            completed=quest.completed_at is not None,
            claimed=quest.claimed_at is not None,
        )
        for quest, template in rows
    ]
    points = _points(rows)
    chests = [
        ActivityChestRead(
            tier=chest.tier,
            name=chest.name,
            milestone=chest.milestone,
            reward_gold=chest.reward_gold,
            reward_exp=chest.reward_exp,
            rolls_item=chest.rolls_item,
            reached=points >= chest.milestone,
            claimed=chest.milestone in opened,
        )
        for chest in CHESTS
    ]
    claimable = sum(1 for quest in quests if quest.completed and not quest.claimed) + sum(
        1 for chest in chests if chest.reached and not chest.claimed
    )
    return DailyQuestsRead(
        quest_date=quest_day(now),
        resets_at=next_reset_at(now),
        activity_points=points,
        max_activity_points=MAX_ACTIVITY_POINTS,
        quests=quests,
        chests=chests,
        claimable_count=claimable,
    )
