from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime
from random import Random

from app.services.game.daily_quests import (
    CHESTS,
    DAILY_SLOTS,
    MAX_ACTIVITY_POINTS,
    POINTS_BY_DIFFICULTY,
    PVP_TYPES,
    QuestDifficulty,
    QuestEvent,
    QuestType,
    activity_points,
    chest_for_milestone,
    chests_reached,
    draw_seed,
    next_reset_at,
    pick_daily_quests,
    progress_after,
    quest_day,
)
from app.services.game.quest_catalog import QUEST_TEMPLATES, quest_title


@dataclass(frozen=True)
class Template:
    id: str
    quest_type: str
    difficulty: str


def _draw(user_id: str, day: date) -> list:
    return pick_daily_quests(QUEST_TEMPLATES, Random(draw_seed(user_id, day)))  # noqa: S311


def test_a_day_is_one_hard_two_medium_and_one_easy_quest_worth_exactly_the_max() -> None:
    for n in range(300):
        drawn = _draw(f"user-{n}", date(2026, 9, 19))

        assert [spec.difficulty for spec in drawn] == list(DAILY_SLOTS)
        assert sum(spec.activity_points for spec in drawn) == MAX_ACTIVITY_POINTS == 100


def test_a_day_never_repeats_a_type_or_holds_more_than_one_pvp_quest() -> None:
    for n in range(300):
        drawn = _draw(f"user-{n}", date(2026, 9, 19))

        types = [spec.quest_type for spec in drawn]
        assert len(set(types)) == len(types)
        assert sum(1 for quest_type in types if quest_type in PVP_TYPES) <= 1


def test_the_same_player_and_day_always_draw_the_same_set() -> None:
    """What makes two requests racing to draw a day's set harmless."""
    day = date(2026, 9, 19)

    assert _draw("alice", day) == _draw("alice", day)


def test_the_draw_varies_between_players_and_between_days() -> None:
    sets = {tuple(spec.code for spec in _draw(f"user-{n}", date(2026, 9, 19))) for n in range(50)}
    days = {tuple(spec.code for spec in _draw("alice", date(2026, 9, d))) for d in range(1, 29)}

    assert len(sets) > 10
    assert len(days) > 10


def test_every_template_gets_drawn_sooner_or_later() -> None:
    seen: Counter[str] = Counter()
    for n in range(500):
        seen.update(spec.code for spec in _draw(f"user-{n}", date(2026, 9, 19)))

    assert set(seen) == {spec.code for spec in QUEST_TEMPLATES}


def test_a_thin_catalog_yields_fewer_quests_rather_than_looping() -> None:
    thin = [
        Template("a", QuestType.CORRECT_ANSWERS, QuestDifficulty.EASY),
        Template("b", QuestType.PLAY_PVP, QuestDifficulty.MEDIUM),
        Template("c", QuestType.WIN_PVP, QuestDifficulty.MEDIUM),
    ]

    drawn = pick_daily_quests(thin, Random(1))  # noqa: S311

    # One medium PvP at most, and no hard quest to be had.
    assert sorted(template.id for template in drawn) in (["a", "b"], ["a", "c"])


def test_the_draw_reads_rows_whose_enums_are_plain_strings() -> None:
    rows = [
        Template(spec.code, spec.quest_type.value, spec.difficulty.value)
        for spec in QUEST_TEMPLATES
    ]

    drawn = pick_daily_quests(rows, Random(7))  # noqa: S311

    assert [row.difficulty for row in drawn] == [slot.value for slot in DAILY_SLOTS]


def test_every_catalog_entry_pays_by_its_difficulty_and_names_its_target() -> None:
    for spec in QUEST_TEMPLATES:
        assert spec.activity_points == POINTS_BY_DIFFICULTY[spec.difficulty]
        assert spec.target > 0
        assert str(spec.target) in quest_title(spec.title, spec.target)
        assert len(spec.code) <= 32


def test_counting_quests_add_up_and_stop_at_the_target() -> None:
    assert progress_after(QuestType.CORRECT_ANSWERS, 10, 15, 3) == 13
    assert progress_after(QuestType.CORRECT_ANSWERS, 13, 15, 9) == 15


def test_a_combo_quest_keeps_the_best_run_instead_of_adding_runs() -> None:
    assert progress_after(QuestType.BEST_COMBO, 3, 8, 4) == 4
    assert progress_after(QuestType.BEST_COMBO, 6, 8, 2) == 6
    assert progress_after(QuestType.BEST_COMBO, 6, 8, 12) == 8


def test_nothing_moves_a_quest_by_zero() -> None:
    assert progress_after(QuestType.WIN_BATTLES, 1, 2, 0) == 1


def test_an_event_touches_only_what_it_counted() -> None:
    event = QuestEvent(correct_answers=7, best_combo=3, battles_won=0)

    assert event.touched() == {QuestType.CORRECT_ANSWERS: 7, QuestType.BEST_COMBO: 3}


def test_an_event_can_answer_for_every_quest_type() -> None:
    event = QuestEvent()

    assert all(event.amount_for(quest_type) == 0 for quest_type in QuestType)


def test_chests_open_at_thirty_sixty_and_a_hundred_points() -> None:
    assert [chest.milestone for chest in CHESTS] == [30, 60, 100]
    assert chests_reached(29) == []
    assert [chest.milestone for chest in chests_reached(55)] == [30]
    assert [chest.milestone for chest in chests_reached(100)] == [30, 60, 100]
    assert chest_for_milestone(60) is CHESTS[1]
    assert chest_for_milestone(50) is None
    # Only the last chest rolls an item.
    assert [chest.rolls_item for chest in CHESTS] == [False, False, True]


def test_activity_never_passes_the_max() -> None:
    assert activity_points([30, 25]) == 55
    assert activity_points([30, 25, 25, 20, 30]) == MAX_ACTIVITY_POINTS


def test_the_day_turns_over_at_midnight_in_vietnam_not_in_utc() -> None:
    # 23:59 and 00:00 in Vietnam are 16:59 and 17:00 UTC.
    before = datetime(2026, 9, 19, 16, 59, tzinfo=UTC)
    after = datetime(2026, 9, 19, 17, 0, tzinfo=UTC)

    assert quest_day(before) == date(2026, 9, 19)
    assert quest_day(after) == date(2026, 9, 20)
    assert next_reset_at(before) == after
    assert next_reset_at(after) == datetime(2026, 9, 20, 17, 0, tzinfo=UTC)
