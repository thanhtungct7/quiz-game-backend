"""Daily quests: which four a player gets today, and what their activity is worth.

Pure functions with no I/O, in the same shape as `streak.py` and `loot.py`.
`today` and the random source are always parameters.

A day's set is four quests -- one easy, two medium, one hard -- whose activity
points always add up to exactly `MAX_ACTIVITY_POINTS`, so the three chests at
30/60/100 line up with finishing one, two or all of them however the draw went.

There is no midnight job. A quest belongs to the calendar day it was drawn for
(in the learner's timezone, see `streak.py`), so "resetting" is nothing more
than the next day having no rows yet. The draw is seeded by (user, day): two
requests racing to create the same day's set draw the same four quests, and the
unique constraint on the table lets exactly one of them in.

Progress is measured when a lesson battle, a duo match, a lesson or a
conversation *ends*, never per answer, so nothing here touches the real-time
loops.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from random import Random
from typing import Protocol

from app.services.game.streak import STREAK_TZ


class QuestType(StrEnum):
    """What a quest counts. At most one quest of each type is drawn per day."""

    CORRECT_ANSWERS = "CORRECT_ANSWERS"
    BEST_COMBO = "BEST_COMBO"
    WIN_BATTLES = "WIN_BATTLES"
    FLAWLESS_WIN = "FLAWLESS_WIN"
    NEW_LESSONS = "NEW_LESSONS"
    PLAY_PVP = "PLAY_PVP"
    WIN_PVP = "WIN_PVP"
    AI_CONVERSATION = "AI_CONVERSATION"


class QuestDifficulty(StrEnum):
    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


# One easy, two medium, one hard: 20 + 25 + 25 + 30 = 100.
POINTS_BY_DIFFICULTY: dict[QuestDifficulty, int] = {
    QuestDifficulty.EASY: 20,
    QuestDifficulty.MEDIUM: 25,
    QuestDifficulty.HARD: 30,
}
DAILY_SLOTS: tuple[QuestDifficulty, ...] = (
    QuestDifficulty.HARD,
    QuestDifficulty.MEDIUM,
    QuestDifficulty.MEDIUM,
    QuestDifficulty.EASY,
)
MAX_ACTIVITY_POINTS = sum(POINTS_BY_DIFFICULTY[slot] for slot in DAILY_SLOTS)

# A duo match needs somebody else online, so at most one of the four quests may
# depend on finding an opponent.
PVP_TYPES = frozenset({QuestType.PLAY_PVP, QuestType.WIN_PVP})
MAX_PVP_QUESTS_PER_DAY = 1

# Quests measured as "the best this run managed" rather than "how many so far".
PEAK_TYPES = frozenset({QuestType.BEST_COMBO})

# Attempts at a draw that satisfies every rule before settling for less. The
# real catalog satisfies them on the first try almost every time; the cap only
# exists so a thin catalog degrades to fewer quests instead of looping.
_DRAW_ATTEMPTS = 50


class ChestTier(StrEnum):
    BRONZE = "BRONZE"
    SILVER = "SILVER"
    GOLD = "GOLD"


@dataclass(frozen=True)
class ChestSpec:
    tier: ChestTier
    name: str
    milestone: int
    reward_gold: int
    reward_exp: int
    # The gold chest also rolls one item from the loot table, the same roll a
    # won boss fight makes.
    rolls_item: bool = False


CHESTS: tuple[ChestSpec, ...] = (
    ChestSpec(ChestTier.BRONZE, "Rương Đồng", 30, reward_gold=15, reward_exp=0),
    ChestSpec(ChestTier.SILVER, "Rương Bạc", 60, reward_gold=30, reward_exp=30),
    ChestSpec(ChestTier.GOLD, "Rương Vàng", 100, reward_gold=60, reward_exp=60, rolls_item=True),
)


def chest_for_milestone(milestone: int) -> ChestSpec | None:
    return next((chest for chest in CHESTS if chest.milestone == milestone), None)


class QuestTemplateView(Protocol):
    """What the draw needs from a template.

    A protocol for the same reason `achievements.Threshold` is one: the draw
    runs over database rows in production and over plain objects in tests.
    Rows store the two enums as plain strings, which compare equal to them.
    """

    @property
    def id(self) -> str: ...

    @property
    def quest_type(self) -> str: ...

    @property
    def difficulty(self) -> str: ...


def draw_seed(user_id: str, day: date) -> str:
    return f"{user_id}:{day.isoformat()}"


def pick_daily_quests[T: QuestTemplateView](templates: Sequence[T], rng: Random) -> list[T]:
    """Four templates for one day, hardest first.

    Rules, in order of importance: one per slot in `DAILY_SLOTS`, never two of
    the same type, and at most `MAX_PVP_QUESTS_PER_DAY` PvP quests. The draw is
    retried a few times rather than solved exactly, because the catalog is
    small and wide enough that a random fill almost always works. When no
    attempt fills every slot, the fullest one is returned.
    """
    by_difficulty: dict[str, list[T]] = {}
    for template in sorted(templates, key=lambda t: t.id):
        by_difficulty.setdefault(template.difficulty, []).append(template)

    best: list[T] = []
    for _ in range(_DRAW_ATTEMPTS):
        chosen: list[T] = []
        for slot in DAILY_SLOTS:
            candidates = [
                template for template in by_difficulty.get(slot, []) if _fits(template, chosen)
            ]
            if candidates:
                chosen.append(rng.choice(candidates))
        if len(chosen) == len(DAILY_SLOTS):
            return chosen
        if len(chosen) > len(best):
            best = chosen
    return best


def _fits(template: QuestTemplateView, chosen: Sequence[QuestTemplateView]) -> bool:
    if any(other.quest_type == template.quest_type for other in chosen):
        return False
    if template.quest_type in PVP_TYPES:
        return sum(1 for other in chosen if other.quest_type in PVP_TYPES) < MAX_PVP_QUESTS_PER_DAY
    return True


@dataclass(frozen=True)
class QuestEvent:
    """What one finished activity contributed. Zero means "no part in it".

    Built by whoever finished the activity: a lesson battle fills in the first
    four, a duo match the answers, the combo and the two PvP fields, and so on.
    """

    correct_answers: int = 0
    best_combo: int = 0
    battles_won: int = 0
    flawless_wins: int = 0
    new_lessons: int = 0
    pvp_played: int = 0
    pvp_won: int = 0
    ai_conversations: int = 0

    def amount_for(self, quest_type: QuestType) -> int:
        return {
            QuestType.CORRECT_ANSWERS: self.correct_answers,
            QuestType.BEST_COMBO: self.best_combo,
            QuestType.WIN_BATTLES: self.battles_won,
            QuestType.FLAWLESS_WIN: self.flawless_wins,
            QuestType.NEW_LESSONS: self.new_lessons,
            QuestType.PLAY_PVP: self.pvp_played,
            QuestType.WIN_PVP: self.pvp_won,
            QuestType.AI_CONVERSATION: self.ai_conversations,
        }[quest_type]

    def touched(self) -> dict[QuestType, int]:
        """Every quest type this event moves, and by how much."""
        amounts = {quest_type: self.amount_for(quest_type) for quest_type in QuestType}
        return {quest_type: amount for quest_type, amount in amounts.items() if amount > 0}


def progress_after(quest_type: str, current: int, target: int, amount: int) -> int:
    """Where a quest stands after an event, never past its target.

    A combo quest asks for one run of N, so it keeps the best run rather than
    adding runs together; everything else accumulates.
    """
    if amount <= 0:
        return current
    if quest_type in PEAK_TYPES:
        return min(target, max(current, amount))
    return min(target, current + amount)


def activity_points(completed_points: Sequence[int]) -> int:
    """Today's activity: the points of every quest finished, capped at the max."""
    return min(MAX_ACTIVITY_POINTS, sum(completed_points))


def chests_reached(points: int) -> list[ChestSpec]:
    return [chest for chest in CHESTS if points >= chest.milestone]


def quest_day(now: datetime) -> date:
    """The day a quest set belongs to. Same clock as the study streak."""
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(STREAK_TZ).date()


def next_reset_at(now: datetime) -> datetime:
    """When today's set expires: the next local midnight, as a UTC instant."""
    tomorrow = quest_day(now) + timedelta(days=1)
    return datetime.combine(tomorrow, time.min, tzinfo=STREAK_TZ).astimezone(UTC)
