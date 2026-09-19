from datetime import UTC, date, datetime, timedelta
from random import Random

import httpx
import pytest

from app.api.dependencies import get_current_user, get_daily_quest_service
from app.core.exceptions import (
    ActivityChestAlreadyClaimedError,
    ActivityChestLockedError,
    ActivityChestNotFoundError,
    DailyQuestAlreadyClaimedError,
    DailyQuestExpiredError,
    DailyQuestNotCompletedError,
    DailyQuestNotFoundError,
)
from app.main import app
from app.models.auth.user import User
from app.models.game.daily_quest import DailyQuestTemplate, UserDailyQuest
from app.models.game.game_item import GameItem, ItemKind, ItemRarity
from app.models.game.gold_transaction import GoldReason
from app.models.game.user_game_profile import UserGameProfile
from app.services.game.daily_quest_service import DailyQuestService, DailyQuestTracker
from app.services.game.daily_quests import (
    PEAK_TYPES,
    QuestDifficulty,
    QuestEvent,
    QuestType,
    progress_after,
)
from app.services.game.leveling import exp_for_level

USER = "user-1"
# 10:00 on the 19th in Vietnam.
NOW = datetime(2026, 9, 19, 3, 0, tzinfo=UTC)
TODAY = date(2026, 9, 19)
TOMORROW = NOW + timedelta(days=1)


def _template(
    code: str, quest_type: QuestType, difficulty: QuestDifficulty, target: int, points: int
) -> DailyQuestTemplate:
    return DailyQuestTemplate(
        id=f"t-{code}",
        code=code,
        quest_type=quest_type.value,
        difficulty=difficulty.value,
        title="Làm {n} lần",
        target=target,
        activity_points=points,
        reward_gold=points,
        reward_exp=2 * points,
        sort_order=0,
        is_active=True,
    )


# Exactly one template per slot, so every draw is this set and the tests can
# say which quest is which. 30 + 25 + 25 + 20 = 100.
WIN_2 = _template("WIN_2", QuestType.WIN_BATTLES, QuestDifficulty.HARD, 2, 30)
CORRECT_10 = _template("CORRECT_10", QuestType.CORRECT_ANSWERS, QuestDifficulty.MEDIUM, 10, 25)
COMBO_5 = _template("COMBO_5", QuestType.BEST_COMBO, QuestDifficulty.MEDIUM, 5, 25)
CHAT_1 = _template("CHAT_1", QuestType.AI_CONVERSATION, QuestDifficulty.EASY, 1, 20)


class FakeQuests:
    """In-memory `DailyQuestRepository`, with the table's unique constraints."""

    def __init__(self, templates: list[DailyQuestTemplate] | None = None) -> None:
        self.templates = templates or [WIN_2, CORRECT_10, COMBO_5, CHAT_1]
        self.rows: list[UserDailyQuest] = []
        self.chests: dict[tuple[str, date, int], str] = {}
        self.rollbacks = 0
        self.broken = False

    def _template(self, template_id: str) -> DailyQuestTemplate:
        return next(t for t in self.templates if t.id == template_id)

    async def rollback(self) -> None:
        self.rollbacks += 1

    async def active_templates(self) -> list[DailyQuestTemplate]:
        return [t for t in self.templates if t.is_active]

    async def quests_for_day(
        self, user_id: str, day: date
    ) -> list[tuple[UserDailyQuest, DailyQuestTemplate]]:
        rows = [q for q in self.rows if q.user_id == user_id and q.quest_date == day]
        return [(q, self._template(q.template_id)) for q in sorted(rows, key=lambda q: q.slot)]

    async def insert_quests(self, rows: list[dict[str, object]]) -> None:
        for row in rows:
            key = (row["user_id"], row["template_id"], row["quest_date"])
            if any((q.user_id, q.template_id, q.quest_date) == key for q in self.rows):
                continue
            self.rows.append(
                UserDailyQuest(
                    id=f"q-{len(self.rows) + 1}", completed_at=None, claimed_at=None, **row
                )
            )

    async def advance(
        self,
        user_id: str,
        day: date,
        quest_type: str,
        amount: int,
        now: datetime,
        *,
        peak: bool,
    ) -> list[str]:
        if self.broken:
            raise RuntimeError("database went away")
        assert peak == (quest_type in PEAK_TYPES)
        finished = []
        for quest in self.rows:
            if (quest.user_id, quest.quest_date, quest.quest_type) != (user_id, day, quest_type):
                continue
            if quest.completed_at is not None:
                continue
            quest.progress = progress_after(quest_type, quest.progress, quest.target, amount)
            if quest.progress >= quest.target:
                quest.completed_at = now
                finished.append(quest.id)
        return finished

    async def completed_since(
        self, user_id: str, since: datetime
    ) -> list[tuple[UserDailyQuest, DailyQuestTemplate]]:
        rows = [
            q
            for q in self.rows
            if q.user_id == user_id and q.completed_at is not None and q.completed_at >= since
        ]
        return [(q, self._template(q.template_id)) for q in rows]

    async def get_quest(self, user_id: str, quest_id: str) -> UserDailyQuest | None:
        return next((q for q in self.rows if q.id == quest_id and q.user_id == user_id), None)

    async def mark_claimed(self, user_id: str, quest_id: str, day: date, now: datetime) -> bool:
        quest = await self.get_quest(user_id, quest_id)
        if quest is None or quest.quest_date != day:
            return False
        if quest.completed_at is None or quest.claimed_at is not None:
            return False
        quest.claimed_at = now
        return True

    async def claimed_chests(self, user_id: str, day: date) -> set[int]:
        return {m for (u, d, m) in self.chests if (u, d) == (user_id, day)}

    async def open_chest(
        self, user_id: str, day: date, milestone: int, now: datetime
    ) -> str | None:
        key = (user_id, day, milestone)
        if key in self.chests:
            return None
        self.chests[key] = f"chest-{len(self.chests) + 1}"
        return self.chests[key]


def _profile(*, total_exp: int = 0, level: int = 1) -> UserGameProfile:
    """Every column spelled out: built outside a session, the model skips its defaults."""
    return UserGameProfile(
        id="profile-1",
        user_id=USER,
        total_exp=total_exp,
        level=level,
        gold=0,
        benchmark_cleared_level=0,
        updated_at=NOW,
    )


class FakeProfiles:
    def __init__(self, profile: UserGameProfile | None = None) -> None:
        self.profile = profile or _profile()

    async def get_or_create(self, user_id: str) -> UserGameProfile:
        return self.profile

    async def save(self, profile: UserGameProfile, data: dict[str, object]) -> UserGameProfile:
        for field, value in data.items():
            setattr(profile, field, value)
        return profile


class FakeLedger:
    def __init__(self) -> None:
        self.rows: list[tuple[str, GoldReason, str, int]] = []

    async def grant(
        self, *, user_id: str, amount: int, reason: GoldReason, ref_id: str, balance_after: int
    ) -> bool:
        if any(row[:3] == (user_id, reason, ref_id) for row in self.rows):
            return False
        self.rows.append((user_id, reason, ref_id, amount))
        return True


class FakeItems:
    def __init__(self) -> None:
        self.pool = [
            GameItem(
                id="item-1",
                code="RUNE_BLADE",
                name="Kiếm cổ ngữ",
                kind=ItemKind.EQUIPMENT,
                rarity=ItemRarity.EPIC,
                is_active=True,
            )
        ]
        self.loot: dict[tuple[str, str], str | None] = {}
        self.inventory: list[tuple[str, str]] = []

    async def drop_pool(self) -> list[GameItem]:
        return list(self.pool)

    async def claim_loot(self, user_id: str, ref_id: str, item_id: str | None) -> bool:
        if (user_id, ref_id) in self.loot:
            return False
        self.loot[(user_id, ref_id)] = item_id
        return True

    async def add_to_inventory(self, user_id: str, item_id: str) -> None:
        self.inventory.append((user_id, item_id))


class World:
    def __init__(self, profile: UserGameProfile | None = None) -> None:
        self.quests = FakeQuests()
        self.profiles = FakeProfiles(profile)
        self.ledger = FakeLedger()
        self.items = FakeItems()

    def service(self) -> DailyQuestService:
        return DailyQuestService(
            quests=self.quests,  # type: ignore[arg-type]
            profiles=self.profiles,  # type: ignore[arg-type]
            ledger=self.ledger,  # type: ignore[arg-type]
            items=self.items,  # type: ignore[arg-type]
            rng=Random(1),  # noqa: S311 - deterministic test fixture
        )

    def tracker(self) -> DailyQuestTracker:
        return DailyQuestTracker(self.quests)  # type: ignore[arg-type]

    def quest(self, template: DailyQuestTemplate, day: date = TODAY) -> UserDailyQuest:
        return next(
            q for q in self.quests.rows if q.template_id == template.id and q.quest_date == day
        )


# --- drawing ------------------------------------------------------------------------


async def test_the_first_look_of_the_day_draws_four_quests_hardest_first() -> None:
    world = World()

    view = await world.service().get_today(USER, NOW)

    codes = [q.code for q in view.quests]
    assert codes[0] == "WIN_2"
    assert sorted(codes[1:3]) == ["COMBO_5", "CORRECT_10"]
    assert codes[3] == "CHAT_1"
    assert view.quest_date == TODAY
    assert view.activity_points == 0
    assert view.max_activity_points == 100
    assert view.claimable_count == 0
    # Midnight in Vietnam.
    assert view.resets_at == datetime(2026, 9, 19, 17, 0, tzinfo=UTC)


async def test_looking_again_keeps_the_same_set() -> None:
    world = World()
    service = world.service()

    first = await service.get_today(USER, NOW)
    second = await service.get_today(USER, NOW + timedelta(hours=5))

    assert [q.id for q in first.quests] == [q.id for q in second.quests]
    assert len(world.quests.rows) == 4


async def test_a_new_day_draws_a_new_set_and_leaves_yesterday_alone() -> None:
    world = World()
    service = world.service()
    await world.tracker().track(USER, QuestEvent(ai_conversations=1), NOW)

    tomorrow = await service.get_today(USER, TOMORROW)

    assert len(world.quests.rows) == 8
    assert all(not q.completed for q in tomorrow.quests)
    assert world.quest(CHAT_1).completed_at == NOW


async def test_a_goal_is_copied_off_the_template_when_the_quest_is_drawn() -> None:
    """Retuning the catalog mid-day must not move a goal already half done."""
    world = World()
    await world.service().get_today(USER, NOW)

    CORRECT_10.target = 99
    try:
        view = await world.service().get_today(USER, NOW)
    finally:
        CORRECT_10.target = 10

    assert next(q for q in view.quests if q.code == "CORRECT_10").target == 10


# --- tracking -----------------------------------------------------------------------


async def test_the_first_activity_of_the_day_counts_before_the_screen_was_opened() -> None:
    world = World()

    finished = await world.tracker().track(USER, QuestEvent(correct_answers=4), NOW)

    assert finished == []
    assert world.quest(CORRECT_10).progress == 4


async def test_a_quest_finishes_at_its_target_and_is_reported_once() -> None:
    world = World()
    tracker = world.tracker()

    first = await tracker.track(USER, QuestEvent(battles_won=1), NOW)
    second = await tracker.track(USER, QuestEvent(battles_won=1), NOW)
    third = await tracker.track(USER, QuestEvent(battles_won=1), NOW)

    assert first == []
    assert [(q.title, q.activity_points) for q in second] == [("Làm 2 lần", 30)]
    assert third == []
    assert world.quest(WIN_2).progress == 2


async def test_types_that_were_not_drawn_today_are_ignored() -> None:
    world = World()

    finished = await world.tracker().track(USER, QuestEvent(pvp_won=1, flawless_wins=1), NOW)

    assert finished == []
    assert all(q.progress == 0 for q in world.quests.rows)


async def test_a_combo_quest_needs_one_run_long_enough() -> None:
    world = World()
    tracker = world.tracker()

    await tracker.track(USER, QuestEvent(best_combo=3), NOW)
    await tracker.track(USER, QuestEvent(best_combo=3), NOW)
    assert world.quest(COMBO_5).completed_at is None

    finished = await tracker.track(USER, QuestEvent(best_combo=6), NOW)
    assert [q.id for q in finished] == [world.quest(COMBO_5).id]
    assert world.quest(COMBO_5).progress == 5


async def test_finishing_quests_fills_the_chest_bar() -> None:
    world = World()
    tracker = world.tracker()
    await tracker.track(USER, QuestEvent(battles_won=2, ai_conversations=1), NOW)

    view = await world.service().get_today(USER, NOW)

    assert view.activity_points == 50
    assert [c.reached for c in view.chests] == [True, False, False]
    # Two quests to collect and one chest to open.
    assert view.claimable_count == 3


async def test_the_quests_a_battle_finished_are_the_ones_completed_since_it_began() -> None:
    world = World()
    tracker = world.tracker()
    await tracker.track(USER, QuestEvent(ai_conversations=1), NOW)
    battle_began = NOW + timedelta(minutes=10)
    await tracker.track(USER, QuestEvent(battles_won=2), NOW + timedelta(minutes=12))

    finished = await tracker.completed_since(USER, battle_began)

    assert [q.id for q in finished] == [world.quest(WIN_2).id]


async def test_a_tracking_failure_is_swallowed_and_the_session_rolled_back() -> None:
    world = World()
    await world.service().get_today(USER, NOW)
    world.quests.broken = True

    finished = await world.tracker().track_quietly(USER, QuestEvent(correct_answers=3), NOW)

    assert finished == []
    assert world.quests.rollbacks == 1


# --- claiming a quest -------------------------------------------------------------------


async def test_claiming_a_finished_quest_pays_its_gold_and_experience_once() -> None:
    world = World()
    service = world.service()
    await world.tracker().track(USER, QuestEvent(battles_won=2), NOW)
    quest = world.quest(WIN_2)

    claim = await service.claim_quest(USER, quest.id, NOW)

    assert claim.reward.gold.delta == WIN_2.reward_gold
    assert claim.reward.exp.delta == WIN_2.reward_exp
    assert claim.reward.loot is None
    assert world.profiles.profile.gold == WIN_2.reward_gold
    assert world.ledger.rows == [(USER, GoldReason.DAILY_QUEST, quest.id, WIN_2.reward_gold)]
    assert next(q for q in claim.quests.quests if q.id == quest.id).claimed is True
    assert claim.quests.claimable_count == 1  # the bronze chest

    with pytest.raises(DailyQuestAlreadyClaimedError):
        await service.claim_quest(USER, quest.id, NOW)
    assert len(world.ledger.rows) == 1


async def test_an_unfinished_quest_cannot_be_claimed() -> None:
    world = World()
    await world.service().get_today(USER, NOW)

    with pytest.raises(DailyQuestNotCompletedError):
        await world.service().claim_quest(USER, world.quest(WIN_2).id, NOW)
    assert world.ledger.rows == []


async def test_another_players_quest_is_not_found() -> None:
    world = World()
    await world.tracker().track(USER, QuestEvent(ai_conversations=1), NOW)

    with pytest.raises(DailyQuestNotFoundError):
        await world.service().claim_quest("someone-else", world.quest(CHAT_1).id, NOW)


async def test_yesterdays_rewards_expire_at_midnight() -> None:
    world = World()
    await world.tracker().track(USER, QuestEvent(ai_conversations=1), NOW)

    with pytest.raises(DailyQuestExpiredError):
        await world.service().claim_quest(USER, world.quest(CHAT_1).id, TOMORROW)


async def test_a_reward_levels_up() -> None:
    world = World(_profile(total_exp=exp_for_level(5) - 10, level=4))
    await world.tracker().track(USER, QuestEvent(battles_won=2), NOW)

    claim = await world.service().claim_quest(USER, world.quest(WIN_2).id, NOW)

    assert (claim.reward.exp.level_before, claim.reward.exp.level_after) == (4, 5)
    assert claim.reward.exp.leveled_up is True


async def test_a_reward_never_levels_past_an_uncleared_benchmark_cap() -> None:
    """The experience is kept; the level waits for the exam, like everywhere else."""
    world = World(_profile(total_exp=exp_for_level(11) - 10, level=10))
    await world.tracker().track(USER, QuestEvent(battles_won=2), NOW)

    claim = await world.service().claim_quest(USER, world.quest(WIN_2).id, NOW)

    assert claim.reward.exp.after == exp_for_level(11) - 10 + WIN_2.reward_exp
    assert claim.reward.exp.level_after == 10
    assert claim.reward.exp.leveled_up is False


# --- chests -------------------------------------------------------------------------------


async def test_a_chest_stays_shut_until_its_milestone() -> None:
    world = World()
    await world.tracker().track(USER, QuestEvent(ai_conversations=1), NOW)  # 20 points

    with pytest.raises(ActivityChestLockedError):
        await world.service().claim_chest(USER, 30, NOW)
    assert world.quests.chests == {}


async def test_a_reached_chest_opens_once() -> None:
    world = World()
    service = world.service()
    await world.tracker().track(USER, QuestEvent(battles_won=2), NOW)  # 30 points

    claim = await service.claim_chest(USER, 30, NOW)

    assert claim.reward.gold.delta == 15
    assert claim.reward.loot is None
    assert [c.claimed for c in claim.quests.chests] == [True, False, False]
    with pytest.raises(ActivityChestAlreadyClaimedError):
        await service.claim_chest(USER, 30, NOW)
    assert [row[1] for row in world.ledger.rows] == [GoldReason.ACTIVITY_CHEST]


async def test_the_gold_chest_also_drops_an_item() -> None:
    world = World()
    await world.tracker().track(
        USER,
        QuestEvent(battles_won=2, correct_answers=10, best_combo=5, ai_conversations=1),
        NOW,
    )

    claim = await world.service().claim_chest(USER, 100, NOW)

    assert claim.reward.loot is not None
    assert claim.reward.loot.code == "RUNE_BLADE"
    assert world.items.inventory == [(USER, "item-1")]


async def test_there_is_no_chest_between_the_milestones() -> None:
    world = World()

    with pytest.raises(ActivityChestNotFoundError):
        await world.service().claim_chest(USER, 50, NOW)


# --- the routes -----------------------------------------------------------------------------


async def _request(world: World, method: str, path: str) -> httpx.Response:
    app.dependency_overrides[get_current_user] = lambda: User(id=USER, email="u@example.com")
    app.dependency_overrides[get_daily_quest_service] = world.service
    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.request(method, f"/api/v1/quests{path}")
    finally:
        app.dependency_overrides.clear()


async def test_the_daily_route_returns_todays_set() -> None:
    world = World()

    response = await _request(world, "GET", "/daily")

    assert response.status_code == 200
    body = response.json()
    assert sorted(q["code"] for q in body["quests"]) == ["CHAT_1", "COMBO_5", "CORRECT_10", "WIN_2"]
    assert [c["milestone"] for c in body["chests"]] == [30, 60, 100]


async def test_the_claim_routes_answer_with_the_right_status() -> None:
    world = World()
    await world.tracker().track(USER, QuestEvent(ai_conversations=1), datetime.now(UTC))
    chat = world.quest(CHAT_1, day=world.quests.rows[0].quest_date)
    win = world.quest(WIN_2, day=chat.quest_date)

    ok = await _request(world, "POST", f"/daily/{chat.id}/claim")
    again = await _request(world, "POST", f"/daily/{chat.id}/claim")
    unfinished = await _request(world, "POST", f"/daily/{win.id}/claim")
    missing = await _request(world, "POST", "/daily/nope/claim")
    locked = await _request(world, "POST", "/daily/chests/30/claim")
    no_chest = await _request(world, "POST", "/daily/chests/50/claim")

    assert ok.status_code == 200
    assert ok.json()["reward"]["gold"]["delta"] == CHAT_1.reward_gold
    assert again.status_code == 409
    assert unfinished.status_code == 400
    assert missing.status_code == 404
    assert locked.status_code == 400
    assert no_chest.status_code == 404
