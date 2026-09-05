"""The two promises the gate catalog makes, checked against the real maths.

Balance data is the one kind of change that breaks nothing and passes
everything: no test fails when a monster's health is halved, it just stops
being a fight. So these read the catalog and the combat functions the engine
actually uses, and state the design out loud -- an ordinary gate is the whole
lesson, a boss is half as long again, and both are survivable.

The reference player is deliberately plain: no class, no equipment, no skills,
no streak, answering every question correctly in five seconds. Everything a
real player accumulates makes the fight easier, so this is the floor.
"""

import pytest

from app.services.game.catalog import MONSTERS, MonsterSpec
from app.services.game.combat import MAX_HP, resolve_blow
from app.services.pve.clock import CORRECT_LOCKOUT_MS, SPEED_REFERENCE_SECONDS
from app.services.pve.monster import MonsterProfile, cast_interval_seconds, monster_attack

# A lesson holds ten questions and an ordinary gate is meant to be the whole
# lesson; a boss is worth going round the pool half again.
ANSWERS_TO_CLEAR = 10
ANSWERS_TO_CLEAR_BOSS = 15

# The reference pace: five seconds of reading and answering, plus the beat the
# server holds between two questions.
ANSWER_MS = 5000
SECONDS_PER_ANSWER = ANSWER_MS / 1000 + CORRECT_LOCKOUT_MS / 1000


def _profile(spec: MonsterSpec) -> MonsterProfile:
    """The catalog row as a battle reads it. Built here rather than through
    `pve.catalog.to_profile`, which takes the ORM row, not the seed spec."""
    return MonsterProfile(
        code=spec.code,
        name=spec.name,
        tier=spec.tier,
        max_hp=spec.max_hp,
        attack_damage=spec.attack_damage,
        damage_reduction_permille=spec.damage_reduction_permille,
        enrage_after_rounds=spec.enrage_after_rounds,
        enrage_multiplier_permille=spec.enrage_multiplier_permille,
        is_boss=spec.is_boss,
        art_code=spec.art_code,
    )


def _answers_to_kill(monster: MonsterProfile) -> int:
    """How many correct answers at the reference pace drop this monster."""
    remaining = monster.max_hp
    for answer in range(1, 60):
        remaining -= resolve_blow(
            is_correct=True,
            elapsed_ms=ANSWER_MS,
            time_limit_seconds=SPEED_REFERENCE_SECONDS,
            combo_count=answer,
            defender_reduction_permille=monster.damage_reduction_permille,
        ).final_damage
        if remaining <= 0:
            return answer
    raise AssertionError(f"{monster.code} never falls")


def _health_left(monster: MonsterProfile, answers: int) -> int:
    """What the player has left after winning in that many answers.

    The monster swings on its own clock, so the only thing that decides how
    much it lands is how long the fight ran.
    """
    duration = (answers - 1) * SECONDS_PER_ANSWER + ANSWER_MS / 1000
    interval = cast_interval_seconds(monster)

    health = MAX_HP
    swing_at = interval
    while swing_at <= duration:
        # `monster_attack` reads the elapsed seconds itself to decide whether
        # the blow is enraged, so the rage threshold needs no arithmetic here.
        health -= monster_attack(monster=monster, elapsed_seconds=swing_at).final_damage
        swing_at += interval
    return health


@pytest.mark.parametrize("spec", MONSTERS, ids=lambda spec: spec.code)
def test_a_gate_takes_a_whole_lesson_to_clear(spec: MonsterSpec) -> None:
    expected = ANSWERS_TO_CLEAR_BOSS if spec.is_boss else ANSWERS_TO_CLEAR
    assert _answers_to_kill(_profile(spec)) == expected


@pytest.mark.parametrize("spec", MONSTERS, ids=lambda spec: spec.code)
def test_a_gate_is_survivable_at_a_plain_pace(spec: MonsterSpec) -> None:
    """A gate must be beatable by a player who brought nothing to it.

    Not comfortably -- the late gates are meant to be close -- but a catalog
    where the reference player cannot win is a wall, not a lesson.
    """
    monster = _profile(spec)
    answers = ANSWERS_TO_CLEAR_BOSS if spec.is_boss else ANSWERS_TO_CLEAR
    assert _health_left(monster, answers) > 0


def test_a_boss_costs_more_health_than_the_gate_it_closes() -> None:
    """The tier's boss has to be the harder of the two, and it is not health
    that makes it so -- it is that the fight lasts long enough to be hit more."""
    by_tier: dict[int, dict[bool, MonsterSpec]] = {}
    for spec in MONSTERS:
        by_tier.setdefault(spec.tier, {})[spec.is_boss] = spec

    for tier, pair in by_tier.items():
        ordinary, boss = pair[False], pair[True]
        ordinary_cost = MAX_HP - _health_left(_profile(ordinary), ANSWERS_TO_CLEAR)
        boss_cost = MAX_HP - _health_left(_profile(boss), ANSWERS_TO_CLEAR_BOSS)
        assert boss_cost > ordinary_cost, f"tier {tier}: boss is the easier fight"


def test_a_boss_winds_up_slower_than_its_tier() -> None:
    """Deliberate, and the opposite of what it used to be.

    A boss is on screen half again as long; swinging more often as well would
    make every boss unwinnable. It takes its time instead, and hits harder for
    it -- which is also the only way the wind-up bar is worth watching.
    """
    for spec in MONSTERS:
        if not spec.is_boss:
            continue
        ordinary = next(o for o in MONSTERS if o.tier == spec.tier and not o.is_boss)
        assert cast_interval_seconds(_profile(spec)) > cast_interval_seconds(_profile(ordinary))
        assert spec.attack_damage > ordinary.attack_damage
