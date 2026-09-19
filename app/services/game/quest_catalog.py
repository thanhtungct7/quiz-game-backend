"""The daily quest catalog: balance data, like `catalog.py`.

Kept in its own module rather than beside the classes and items only because
`catalog.py` is already long; it is seeded the same way, upserted by code on
every start.

Reward scale, for reference: a lesson gate won for the first time pays about
50 EXP and 20 gold, a replay about a third of that (`services/pve/rewards.py`).
A full day of quests and chests comes to roughly 170 gold -- a common skin a
day, a legendary one in about five.
"""

from dataclasses import dataclass

from app.repository.game.daily_quest_repository import DailyQuestRepository
from app.services.game.daily_quests import (
    POINTS_BY_DIFFICULTY,
    QuestDifficulty,
    QuestType,
)

EASY = QuestDifficulty.EASY
MEDIUM = QuestDifficulty.MEDIUM
HARD = QuestDifficulty.HARD

REWARD_GOLD: dict[QuestDifficulty, int] = {EASY: 10, MEDIUM: 15, HARD: 25}
REWARD_EXP: dict[QuestDifficulty, int] = {EASY: 20, MEDIUM: 30, HARD: 50}


@dataclass(frozen=True)
class QuestTemplateSpec:
    code: str
    quest_type: QuestType
    difficulty: QuestDifficulty
    title: str
    target: int

    @property
    def id(self) -> str:
        """So a spec can stand in for a row in `pick_daily_quests`."""
        return self.code

    @property
    def activity_points(self) -> int:
        return POINTS_BY_DIFFICULTY[self.difficulty]

    @property
    def reward_gold(self) -> int:
        return REWARD_GOLD[self.difficulty]

    @property
    def reward_exp(self) -> int:
        return REWARD_EXP[self.difficulty]


# `{n}` in a title is the target. Every difficulty holds enough non-PvP types
# that the draw can always fill its four slots without a match.
QUEST_TEMPLATES: tuple[QuestTemplateSpec, ...] = (
    # --- easy: one short session ------------------------------------------------
    QuestTemplateSpec("Q_CORRECT_15", QuestType.CORRECT_ANSWERS, EASY, "Trả lời đúng {n} câu", 15),
    QuestTemplateSpec(
        "Q_COMBO_5", QuestType.BEST_COMBO, EASY, "Đúng liên tiếp {n} câu trong một trận", 5
    ),
    QuestTemplateSpec("Q_WIN_1", QuestType.WIN_BATTLES, EASY, "Thắng {n} ải", 1),
    # --- medium ---------------------------------------------------------------------
    QuestTemplateSpec(
        "Q_CORRECT_30", QuestType.CORRECT_ANSWERS, MEDIUM, "Trả lời đúng {n} câu", 30
    ),
    QuestTemplateSpec(
        "Q_COMBO_8", QuestType.BEST_COMBO, MEDIUM, "Đúng liên tiếp {n} câu trong một trận", 8
    ),
    QuestTemplateSpec("Q_WIN_2", QuestType.WIN_BATTLES, MEDIUM, "Thắng {n} ải", 2),
    QuestTemplateSpec("Q_LESSON_1", QuestType.NEW_LESSONS, MEDIUM, "Hoàn thành {n} bài học mới", 1),
    QuestTemplateSpec("Q_PVP_PLAY_1", QuestType.PLAY_PVP, MEDIUM, "Chơi {n} trận đối kháng", 1),
    QuestTemplateSpec(
        "Q_AI_CHAT_1", QuestType.AI_CONVERSATION, MEDIUM, "Hoàn thành {n} hội thoại với AI", 1
    ),
    # --- hard -----------------------------------------------------------------------
    QuestTemplateSpec("Q_CORRECT_60", QuestType.CORRECT_ANSWERS, HARD, "Trả lời đúng {n} câu", 60),
    QuestTemplateSpec(
        "Q_COMBO_12", QuestType.BEST_COMBO, HARD, "Đúng liên tiếp {n} câu trong một trận", 12
    ),
    QuestTemplateSpec("Q_WIN_4", QuestType.WIN_BATTLES, HARD, "Thắng {n} ải", 4),
    QuestTemplateSpec(
        "Q_FLAWLESS_1", QuestType.FLAWLESS_WIN, HARD, "Thắng {n} ải mà không mất máu", 1
    ),
    QuestTemplateSpec("Q_LESSON_2", QuestType.NEW_LESSONS, HARD, "Hoàn thành {n} bài học mới", 2),
    QuestTemplateSpec("Q_PVP_WIN_1", QuestType.WIN_PVP, HARD, "Thắng {n} trận đối kháng", 1),
)


def quest_title(template_title: str, target: int) -> str:
    return template_title.replace("{n}", str(target))


def _template_row(spec: QuestTemplateSpec, sort_order: int) -> dict[str, object]:
    return {
        "quest_type": spec.quest_type.value,
        "difficulty": spec.difficulty.value,
        "title": spec.title,
        "target": spec.target,
        "activity_points": spec.activity_points,
        "reward_gold": spec.reward_gold,
        "reward_exp": spec.reward_exp,
        "sort_order": sort_order,
        "is_active": True,
    }


async def seed_quest_catalog(quests: DailyQuestRepository) -> None:
    """Upsert the quest list. Safe to run on every start."""
    for index, spec in enumerate(QUEST_TEMPLATES):
        await quests.upsert_template(spec.code, _template_row(spec, index))
