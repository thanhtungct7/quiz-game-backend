from datetime import UTC, datetime, timedelta

import pytest

from app.core.exceptions import NotEnoughEnergyError
from app.models.game.user_game_profile import MAX_ENERGY as MODEL_MAX_ENERGY
from app.services.game.energy import (
    MAX_ENERGY,
    REGEN_INTERVAL,
    REVIEW_REFILL,
    current_energy,
    next_regen_at,
    refill,
    regenerate,
    spend,
)

NOW = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def test_the_database_default_matches_the_rule() -> None:
    assert MODEL_MAX_ENERGY == MAX_ENERGY


def test_a_full_bar_does_not_grow() -> None:
    assert current_energy(MAX_ENERGY, NOW - timedelta(days=3), NOW) == MAX_ENERGY


def test_energy_returns_one_point_per_interval() -> None:
    assert current_energy(0, NOW - REGEN_INTERVAL, NOW) == 1
    assert current_energy(0, NOW - REGEN_INTERVAL * 3, NOW) == 3


def test_regeneration_stops_at_the_cap() -> None:
    assert current_energy(0, NOW - REGEN_INTERVAL * 99, NOW) == MAX_ENERGY


def test_a_partial_interval_is_carried_not_discarded() -> None:
    # Ninety minutes is three half-hours plus ten minutes.
    stored, stamp = regenerate(0, NOW - timedelta(minutes=100), NOW)
    assert stored == 3
    # The leftover ten minutes still counts toward the fourth point: reading
    # the balance must not reset the clock.
    assert current_energy(stored, stamp, NOW + timedelta(minutes=20)) == 4


def test_reading_repeatedly_never_conjures_energy() -> None:
    stored, stamp = 0, NOW - timedelta(minutes=29)
    for _ in range(10):
        stored, stamp = regenerate(stored, stamp, NOW)
    assert stored == 0


def test_a_timestamp_in_the_future_is_ignored_rather_than_punished() -> None:
    assert current_energy(2, NOW + timedelta(hours=5), NOW) == 2


def test_spending_takes_a_point() -> None:
    remaining, _ = spend(3, NOW, NOW, 1)
    assert remaining == 2


def test_spending_an_empty_bar_raises() -> None:
    with pytest.raises(NotEnoughEnergyError):
        spend(0, NOW, NOW, 1)


def test_leaving_a_full_bar_starts_the_clock_now() -> None:
    remaining, stamp = spend(MAX_ENERGY, NOW - timedelta(days=2), NOW, 1)
    assert remaining == MAX_ENERGY - 1
    # The next point is a whole interval away, not instantly available.
    assert stamp == NOW
    assert current_energy(remaining, stamp, NOW) == MAX_ENERGY - 1


def test_spending_counts_what_regenerated_first() -> None:
    remaining, _ = spend(0, NOW - REGEN_INTERVAL * 2, NOW, 1)
    assert remaining == 1


def test_finishing_a_lesson_refills_faster_than_waiting() -> None:
    restored, _ = refill(1, NOW, NOW, REVIEW_REFILL)
    assert restored == 1 + REVIEW_REFILL
    assert REVIEW_REFILL > 1


def test_a_refill_never_overflows() -> None:
    restored, stamp = refill(MAX_ENERGY - 1, NOW, NOW, 99)
    assert restored == MAX_ENERGY
    assert stamp == NOW


def test_next_regen_is_one_interval_out_and_none_when_full() -> None:
    assert next_regen_at(MAX_ENERGY, NOW, NOW) is None
    assert next_regen_at(0, NOW, NOW) == NOW + REGEN_INTERVAL


def test_next_regen_accounts_for_time_already_served() -> None:
    stamp = NOW - timedelta(minutes=20)
    assert next_regen_at(0, stamp, NOW) == stamp + REGEN_INTERVAL
