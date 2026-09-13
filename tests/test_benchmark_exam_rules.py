from datetime import UTC, datetime, timedelta

from app.models.content.lesson import Lesson
from app.services.game.benchmark_exam import (
    EXPIRY_GRACE,
    MAX_SOURCE_UNITS,
    PathUnit,
    accepts_answers_at,
    group_path,
    is_passing,
    percent,
    pick_source_units,
    spread,
)


def _units(count: int, lessons_each: int = 2) -> list[PathUnit]:
    return [
        PathUnit(f"u{u}", tuple(f"u{u}-l{lesson}" for lesson in range(lessons_each)))
        for u in range(count)
    ]


def _finish(*units: PathUnit) -> set[str]:
    return {lesson for unit in units for lesson in unit.lesson_ids}


def test_a_short_list_is_taken_whole() -> None:
    assert spread(["a", "b"], 3) == ["a", "b"]


def test_a_long_list_is_sampled_first_middle_and_last() -> None:
    assert spread(["a", "b", "c", "d", "e"], 3) == ["a", "c", "e"]


def test_the_paper_is_drawn_from_finished_units_spread_across_the_path() -> None:
    units = _units(7)

    picked = pick_source_units(units, _finish(*units[:5]))

    assert [unit.unit_id for unit in picked] == ["u0", "u2", "u4"]
    assert len(picked) == MAX_SOURCE_UNITS


def test_a_started_unit_is_used_when_none_is_finished() -> None:
    units = _units(3)

    picked = pick_source_units(units, {"u1-l0"})

    assert [unit.unit_id for unit in picked] == ["u1"]


def test_the_opening_unit_is_the_last_resort() -> None:
    units = _units(3)

    assert [unit.unit_id for unit in pick_source_units(units, set())] == ["u0"]


def test_no_path_draws_from_nothing() -> None:
    assert pick_source_units([], set()) == []


def test_the_path_is_folded_into_units_in_order() -> None:
    lessons = [
        Lesson(id="l1", unit_id="u1", title="1", order_index=1),
        Lesson(id="l2", unit_id="u1", title="2", order_index=2),
        Lesson(id="l3", unit_id="u2", title="3", order_index=1),
    ]

    assert group_path(lessons) == [PathUnit("u1", ("l1", "l2")), PathUnit("u2", ("l3",))]


def test_eighty_percent_is_a_pass_and_one_less_is_not() -> None:
    assert is_passing(24, 30, 80)
    assert not is_passing(23, 30, 80)


def test_an_empty_paper_is_never_a_pass() -> None:
    assert not is_passing(0, 0, 80)
    assert percent(0, 0) == 0


def test_answers_are_taken_until_the_grace_runs_out() -> None:
    expires = datetime(2026, 9, 13, 12, 0, tzinfo=UTC)

    assert accepts_answers_at(expires, expires + EXPIRY_GRACE)
    assert not accepts_answers_at(expires, expires + EXPIRY_GRACE + timedelta(seconds=1))
