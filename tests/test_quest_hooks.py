"""What each finished activity counts toward the daily quests.

The quest service itself is covered in `test_daily_quest_service.py`; these
pin down the translation from a battle, a match or a lesson into a
`QuestEvent`, which is where a quest would silently stop counting.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast

from app.models.duo.duo_match import DuoMatchEndReason
from app.models.game.user_game_profile import UserGameProfile
from app.models.pve.lesson_battle import BattleStatus
from app.services.duo import persistence as duo_persistence
from app.services.duo.persistence import MatchResult
from app.services.duo.scoring import MatchOutcome
from app.services.game.daily_quests import QuestEvent
from app.services.game.lesson_rewards import LessonRewardService
from app.services.pve import persistence as pve_persistence


def _battle(*, correct: int = 6, combo: int = 4, took_damage: bool = False) -> Any:
    return SimpleNamespace(correct_count=correct, best_combo=combo, took_damage=took_damage)


def test_a_won_battle_counts_answers_the_win_and_a_clean_sheet() -> None:
    event = pve_persistence._quest_event(_battle(), BattleStatus.WON)

    assert event == QuestEvent(correct_answers=6, best_combo=4, battles_won=1, flawless_wins=1)


def test_a_win_that_took_a_hit_is_not_flawless() -> None:
    event = pve_persistence._quest_event(_battle(took_damage=True), BattleStatus.WON)

    assert event.battles_won == 1
    assert event.flawless_wins == 0


def test_a_lost_or_abandoned_battle_still_keeps_its_answers() -> None:
    for status in (BattleStatus.LOST, BattleStatus.ABANDONED):
        event = pve_persistence._quest_event(_battle(), status)

        assert event == QuestEvent(correct_answers=6, best_combo=4)


def _match() -> Any:
    return SimpleNamespace(
        players={
            "win": SimpleNamespace(correct_count=8, best_combo=5),
            "lose": SimpleNamespace(correct_count=3, best_combo=2),
        }
    )


def _result(forfeit: str | None = None, draw: bool = False) -> MatchResult:
    outcomes = (
        {"win": MatchOutcome.DRAW, "lose": MatchOutcome.DRAW}
        if draw
        else {"win": MatchOutcome.WIN, "lose": MatchOutcome.LOSE}
    )
    return MatchResult(
        outcome_by_user=outcomes,
        winner_id=None if draw else "win",
        end_reason=DuoMatchEndReason.OPPONENT_LEFT if forfeit else DuoMatchEndReason.TIME_UP,
        duration_seconds=90,
        forfeit_user_id=forfeit,
    )


def test_a_match_counts_as_played_for_both_and_won_for_the_winner() -> None:
    match, result = _match(), _result()

    assert duo_persistence._quest_event(match, result, "win") == QuestEvent(
        correct_answers=8, best_combo=5, pvp_played=1, pvp_won=1
    )
    assert duo_persistence._quest_event(match, result, "lose") == QuestEvent(
        correct_answers=3, best_combo=2, pvp_played=1
    )


def test_walking_out_of_a_match_is_not_a_match_played() -> None:
    event = duo_persistence._quest_event(_match(), _result(forfeit="lose"), "lose")

    assert event.pvp_played == 0
    assert event.correct_answers == 3


def test_a_draw_is_played_but_not_won() -> None:
    event = duo_persistence._quest_event(_match(), _result(draw=True), "win")

    assert (event.pvp_played, event.pvp_won) == (1, 0)


class _Profiles:
    def __init__(self) -> None:
        self.profile = UserGameProfile(
            id="p",
            user_id="u",
            total_exp=0,
            level=1,
            gold=0,
            energy=0,
            energy_updated_at=datetime.now(UTC),
            day_streak=0,
            best_day_streak=0,
            last_active_date=None,
        )

    async def get_or_create(self, user_id: str) -> UserGameProfile:
        return self.profile

    async def save(self, profile: UserGameProfile, data: dict[str, object]) -> UserGameProfile:
        for field, value in data.items():
            setattr(profile, field, value)
        return profile


class _Activity:
    async def record(self, user_id: str, day: date, *, lessons: int = 0, matches: int = 0) -> None:
        return None


class _Tracker:
    def __init__(self) -> None:
        self.events: list[QuestEvent] = []

    async def track_quietly(self, user_id: str, event: QuestEvent, now: datetime) -> list[Any]:
        self.events.append(event)
        return []


async def test_completing_a_lesson_counts_one_new_lesson() -> None:
    tracker = _Tracker()
    rewards = LessonRewardService(
        profiles=cast(Any, _Profiles()),
        activity=cast(Any, _Activity()),
        quests=cast(Any, tracker),
    )

    await rewards.on_lesson_completed("u")

    assert tracker.events == [QuestEvent(new_lessons=1)]
